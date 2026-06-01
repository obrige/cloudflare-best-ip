#!/usr/bin/env python3
"""
Cloudflare IP Optimizer — 通过 https://cdnjs.cloudflare.com/cdn-cgi/trace 测试HTTPS延迟和 colo
"""

import asyncio
import subprocess
import re
import time
import argparse
import sys
from dataclasses import dataclass
from typing import Optional

HOST = "cdnjs.cloudflare.com"
TRACE_URL = f"https://{HOST}/cdn-cgi/trace"

# Cloudflare 常用 IP 段（IPv4 + IPv6）
DEFAULT_IP_RANGES = [
    # IPv4
    "104.16.0.0/12",
    "104.64.0.0/10",
    # IPv6
    "2606:4700::/44",
    "2400:cb00::/32",
]


@dataclass
class Result:
    ip: str
    colo: str
    loc: str
    latency_ms: float
    http_code: int
    error: Optional[str] = None


def parse_trace(text: str) -> dict:
    """将 trace 响应解析为 dict"""
    data = {}
    for line in text.strip().split("\n"):
        if "=" in line:
            k, v = line.split("=", 1)
            data[k.strip()] = v.strip()
    return data


async def test_ip(ip: str, timeout: float = 5.0) -> Result:
    """
    使用 curl --resolve 向指定 IP 发起 HTTPS 请求，
    测量HTTPS延迟并解析 colo / loc。
    IPv6 地址自动用方括号包裹。
    """
    # IPv6 自动加方括号
    if ":" in ip and ip[0] != "[":
        ip = f"[{ip}]"

    cmd = [
        "curl", "-s",
        "--resolve", f"{HOST}:443:{ip}",
        "--connect-timeout", str(timeout),
        "--max-time", str(timeout),
        "-w", "\n__CURL_TIME__%{time_total}__CURL_CODE__%{http_code}",
        TRACE_URL,
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout + 1
        )
        output = stdout.decode(errors="replace")
    except asyncio.TimeoutError:
        return Result(ip=ip, colo="", loc="", latency_ms=timeout * 1000, http_code=0, error="timeout")
    except Exception as e:
        return Result(ip=ip, colo="", loc="", latency_ms=0, http_code=0, error=str(e))

    # 提取 curl 写入的耗时和状态码
    m = re.search(r"__CURL_TIME__([\d.]+)__CURL_CODE__(\d+)", output)
    if m:
        latency_s = float(m.group(1))
        http_code = int(m.group(2))
        trace_text = output[: output.index("__CURL_TIME__")]
    else:
        latency_s = 0
        http_code = 0
        trace_text = output

    trace = parse_trace(trace_text)
    colo = trace.get("colo", "?")
    loc = trace.get("loc", "?")
    latency_ms = round(latency_s * 1000, 1)

    if http_code != 200:
        return Result(ip=ip, colo=colo, loc=loc, latency_ms=latency_ms, http_code=http_code, error=f"HTTP {http_code}")

    return Result(ip=ip, colo=colo, loc=loc, latency_ms=latency_ms, http_code=http_code)


async def run_tests(ips: list[str], concurrency: int = 20, timeout: float = 5.0) -> list[Result]:
    """并发测试所有 IP，返回按延迟排序的结果列表"""
    sem = asyncio.Semaphore(concurrency)

    async def bounded_test(ip):
        async with sem:
            return await test_ip(ip, timeout)

    print(f"🚀 开始测试 {len(ips)} 个 IP（并发={concurrency} 超时={timeout}s）...")
    t0 = time.monotonic()
    tasks = [bounded_test(ip) for ip in ips]
    results = await asyncio.gather(*tasks)
    elapsed = time.monotonic() - t0
    print(f"✅ 测试完成，耗时 {elapsed:.1f}s\n")

    results.sort(key=lambda r: (r.error is not None, r.latency_ms))
    return results


def print_results(results: list[Result], top_n: int = 30):
    """以表格格式打印结果"""
    ok = [r for r in results if r.error is None]
    err = [r for r in results if r.error is not None]

    print(f"{'='*70}")
    print(f"{'IP':<42} {'延迟':>8} {'Colo':>6} {'Loc':>5} {'状态码':>7}")
    print(f"{'-'*70}")

    for r in ok[:top_n]:
        print(f"{r.ip:<42} {r.latency_ms:>7.1f}ms {r.colo:>6} {r.loc:>5} {r.http_code:>7}")

    if err:
        print(f"\n{'—'*40}")
        print(f"❌ 失败: {len(err)} 个")
        for r in err[:10]:
            print(f"   {r.ip:<42} {r.error}")

    print(f"\n总计: {len(ok)} 成功 / {len(err)} 失败")

    colo_map = {}
    for r in ok:
        colo_map.setdefault(r.colo, []).append(r.latency_ms)
    if colo_map:
        print(f"\n📊 各 Colo 最低延迟:")
        for colo, lats in sorted(colo_map.items()):
            print(f"   {colo:>6}  最低={min(lats):.1f}ms  平均={sum(lats)/len(lats):.1f}ms  (样本={len(lats)})")


def generate_ips_from_ranges(ranges: list[str], per_range: int = 3) -> list[str]:
    """从 CIDR 段中每段抽取若干个 IP（支持 IPv4/IPv6）"""
    import ipaddress
    ips = []
    for r in ranges:
        net = ipaddress.ip_network(r, strict=False)
        hosts = list(net.hosts())
        step = max(len(hosts) // per_range, 1)
        for i in range(0, min(len(hosts), per_range * step), step):
            ips.append(str(hosts[i]))
    return ips


def load_ips(filepath: str) -> list[str]:
    """从文件加载 IP 列表，每行一个"""
    with open(filepath) as f:
        ips = [line.strip() for line in f if line.strip() and not line.startswith("#")]
    return ips


def main():
    parser = argparse.ArgumentParser(description="Cloudflare IP 优选工具 — 测试HTTPS延迟并筛选最优IP")
    parser.add_argument("-f", "--file", default="ips.txt", help="IP 列表文件（每行一个IP）")
    parser.add_argument("-c", "--concurrency", type=int, default=20, help="并发数（默认 20）")
    parser.add_argument("-t", "--timeout", type=float, default=5.0, help="超时秒数（默认 5）")
    parser.add_argument("-n", "--top", type=int, default=30, help="显示前 N 个最优结果")
    parser.add_argument("--generate", action="store_true", help="使用内置 IPv4/IPv6 段生成测试列表")
    parser.add_argument("--per-range", type=int, default=3, help="每段抽取 IP 数（配合 --generate）")
    parser.add_argument("--export", help="导出最优 IP 到文件")
    parser.add_argument("--sort-by-colo", action="store_true", help="按 colo 分组输出")
    args = parser.parse_args()

    if args.generate:
        ips = generate_ips_from_ranges(DEFAULT_IP_RANGES, args.per_range)
        print(f"从 {len(DEFAULT_IP_RANGES)} 个 IP 段生成了 {len(ips)} 个测试 IP")
    else:
        try:
            ips = load_ips(args.file)
        except FileNotFoundError:
            print(f"❌ 文件不存在: {args.file}")
            print(f"   请创建该文件（每行一个 IP），或用 --generate 自动生成")
            sys.exit(1)

    if not ips:
        print("❌ IP 列表为空")
        sys.exit(1)

    results = asyncio.run(run_tests(ips, args.concurrency, args.timeout))

    if args.sort_by_colo:
        colo_groups = {}
        for r in results:
            if r.error is None:
                colo_groups.setdefault(r.colo, []).append(r)
        for colo in sorted(colo_groups):
            print(f"\n--- {colo} ---")
            for r in sorted(colo_groups[colo], key=lambda x: x.latency_ms)[:5]:
                print(f"  {r.ip:<42} {r.latency_ms:.1f}ms")
    else:
        print_results(results, args.top)

    if args.export:
        ok = [r for r in results if r.error is None]
        with open(args.export, "w") as f:
            for r in ok[:args.top]:
                f.write(f"{r.ip}  # {r.colo} {r.latency_ms:.1f}ms\n")
        print(f"\n📁 最优 IP 已导出到: {args.export}")


if __name__ == "__main__":
    main()
