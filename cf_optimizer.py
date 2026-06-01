#!/usr/bin/env python3
"""
Cloudflare IP Optimizer — 通过 https://cdnjs.cloudflare.com/cdn-cgi/trace 测试HTTPS延迟和 colo
IP 列表全部来自外部文件：ipv4.txt / ipv6.txt
CIDR 段随机抽取 per_range 个 IP
输出四个文件到 results/ 目录
"""

import asyncio
import ipaddress
import os
import random
import re
import sys
import time
import argparse
from dataclasses import dataclass
from typing import Optional

HOST = "cdnjs.cloudflare.com"
TRACE_URL = f"https://{HOST}/cdn-cgi/trace"
DEFAULT_IPV4 = "ipv4.txt"
DEFAULT_IPV6 = "ipv6.txt"
DEFAULT_OUT_DIR = "results"

OUT_FULL = "results_full.txt"
OUT_IPV4 = "best_ipv4.txt"
OUT_IPV6 = "best_ipv6.txt"
OUT_ALL  = "best_all.txt"

PROGRESS_EVERY = 100


@dataclass
class Result:
    ip: str
    colo: str
    loc: str
    latency_ms: float
    http_code: int
    error: Optional[str] = None

    def is_v6(self) -> bool:
        return ":" in self.ip


def parse_trace(text: str) -> dict:
    data = {}
    for line in text.strip().split("\n"):
        if "=" in line:
            k, v = line.split("=", 1)
            data[k.strip()] = v.strip()
    return data


def _random_ip_from_net(net: ipaddress.IPv4Network | ipaddress.IPv6Network) -> str:
    num_hosts = net.num_addresses
    if net.version == 4 and num_hosts >= 2:
        offset = random.randint(1, num_hosts - 2)
    elif num_hosts >= 2:
        offset = random.randint(1, num_hosts - 1)
    else:
        offset = 0
    return str(net[offset])


def load_ips(filepath: str, per_range: int = 1) -> list[str]:
    ips = []
    with open(filepath) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "/" in line:
                try:
                    net = ipaddress.ip_network(line, strict=False)
                    for _ in range(per_range):
                        ips.append(_random_ip_from_net(net))
                except ValueError:
                    print(f"⚠️ 跳过无效 CIDR: {line}")
            else:
                try:
                    ipaddress.ip_address(line)
                    ips.append(line)
                except ValueError:
                    print(f"⚠️ 跳过无效 IP: {line}")
    return ips


async def test_ip(ip: str, timeout: float = 5.0) -> Result:
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


def _format_progress(done: int, total: int, ok_cnt: int, elapsed: float) -> str:
    """格式化一行进度信息"""
    pct = done * 100 // total
    bar_len = 20
    filled = pct * bar_len // 100
    bar = "█" * filled + "░" * (bar_len - filled)
    eta = elapsed / done * (total - done) if done > 0 else 0
    return (f"[{done}/{total}] {bar} {pct:>3}%  "
            f"✅{ok_cnt} ❌{done - ok_cnt}  "
            f"⏱{elapsed:.0f}s ETA{eta:.0f}s")


async def run_tests(ips: list[str], concurrency: int = 50, timeout: float = 5.0) -> list[Result]:
    """并发测试，流式收集结果并实时显示进度和当前最优"""
    sem = asyncio.Semaphore(concurrency)
    total = len(ips)

    async def bounded_test(ip):
        async with sem:
            return await test_ip(ip, timeout)

    print(f"🚀 开始测试 {total} 个 IP（并发={concurrency} 超时={timeout}s）...")
    print(f"⏳ 等待首批结果...")

    t0 = time.monotonic()
    tasks = [asyncio.ensure_future(bounded_test(ip)) for ip in ips]

    results: list[Result] = []
    done_count = 0
    ok_count = 0
    next_milestone = PROGRESS_EVERY

    for coro in asyncio.as_completed(tasks):
        r = await coro
        results.append(r)
        done_count += 1
        if r.error is None:
            ok_count += 1

        if done_count >= next_milestone or done_count == total:
            elapsed = time.monotonic() - t0
            prog = _format_progress(done_count, total, ok_count, elapsed)

            # 当前 top 5
            ok_results = sorted(
                [x for x in results if x.error is None],
                key=lambda x: x.latency_ms
            )[:5]
            if ok_results:
                tops = " | ".join(f"{r.latency_ms:.0f}ms({r.colo})" for r in ok_results)
                print(f"  {prog}  🏆 {tops}", flush=True)
            else:
                print(f"  {prog}", flush=True)

            next_milestone = ((done_count // PROGRESS_EVERY) + 1) * PROGRESS_EVERY

    elapsed = time.monotonic() - t0
    print(f"\n✅ 测试完成，耗时 {elapsed:.1f}s\n")

    results.sort(key=lambda r: (r.error is not None, r.latency_ms))
    return results


def print_results(results: list[Result], top_n: int = 100):
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


def write_outputs(results: list[Result], top_n: int, out_dir: str = "results"):
    os.makedirs(out_dir, exist_ok=True)
    ok = [r for r in results if r.error is None]
    err = [r for r in results if r.error is not None]

    v4 = [r for r in ok if not r.is_v6()]
    v6 = [r for r in ok if r.is_v6()]

    path_full = os.path.join(out_dir, OUT_FULL)
    with open(path_full, "w") as f:
        f.write(f"Cloudflare IP Optimizer — 完整测试结果\n")
        f.write(f"测试时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"成功: {len(ok)} / 失败: {len(err)} / 总计: {len(results)}\n")
        f.write(f"\n{'='*70}\n")
        f.write(f"{'IP':<42} {'延迟':>8} {'Colo':>6} {'Loc':>5} {'状态码':>7}\n")
        f.write(f"{'-'*70}\n")
        for r in ok:
            f.write(f"{r.ip:<42} {r.latency_ms:>7.1f}ms {r.colo:>6} {r.loc:>5} {r.http_code:>7}\n")
        if err:
            f.write(f"\n{'—'*40}\n")
            f.write(f"失败: {len(err)} 个\n")
            for r in err:
                f.write(f"{r.ip:<42} {r.error}\n")
    print(f"📄 {path_full}  ({len(ok)} 成功 + {len(err)} 失败)")

    path_v4 = os.path.join(out_dir, OUT_IPV4)
    with open(path_v4, "w") as f:
        for r in v4[:top_n]:
            f.write(f"{r.ip}\n")
    print(f"📄 {path_v4}  ({min(len(v4), top_n)} 个 IPv4)")

    path_v6 = os.path.join(out_dir, OUT_IPV6)
    with open(path_v6, "w") as f:
        for r in v6[:top_n]:
            f.write(f"{r.ip}\n")
    print(f"📄 {path_v6}  ({min(len(v6), top_n)} 个 IPv6)")

    path_all = os.path.join(out_dir, OUT_ALL)
    with open(path_all, "w") as f:
        for r in ok[:top_n]:
            f.write(f"{r.ip}\n")
    print(f"📄 {path_all}  ({min(len(ok), top_n)} 个混合)")


def main():
    parser = argparse.ArgumentParser(
        description="Cloudflare IP Optimizer — 测试HTTPS延迟并筛选最优IP"
    )
    parser.add_argument("-4", "--ipv4", default=DEFAULT_IPV4,
                        help=f"IPv4 列表文件 (默认 {DEFAULT_IPV4})")
    parser.add_argument("-6", "--ipv6", default=DEFAULT_IPV6,
                        help=f"IPv6 列表文件 (默认 {DEFAULT_IPV6})")
    parser.add_argument("--no-ipv4", action="store_true", help="跳过 IPv4")
    parser.add_argument("--no-ipv6", action="store_true", help="跳过 IPv6")
    parser.add_argument("-c", "--concurrency", type=int, default=50,
                        help="并发数（默认 50）")
    parser.add_argument("-t", "--timeout", type=float, default=5.0,
                        help="超时秒数（默认 5）")
    parser.add_argument("-n", "--top", type=int, default=100,
                        help="显示及导出的最优 IP 数量（默认 100）")
    parser.add_argument("-p", "--per-range", type=int, default=1,
                        help="每个 CIDR 段随机抽取 IP 数（默认 1）")
    parser.add_argument("-o", "--out-dir", default=DEFAULT_OUT_DIR,
                        help=f"输出目录（默认 {DEFAULT_OUT_DIR}/）")
    parser.add_argument("--sort-by-colo", action="store_true",
                        help="按 colo 分组输出")
    args = parser.parse_args()

    ips = []
    sources = []

    if not args.no_ipv4:
        try:
            v4 = load_ips(args.ipv4, args.per_range)
            ips += v4
            sources.append(f"{args.ipv4} ({len(v4)} 个, 每段抽{args.per_range})")
        except FileNotFoundError:
            print(f"⚠️ {args.ipv4} 不存在，跳过 IPv4")
    if not args.no_ipv6:
        try:
            v6 = load_ips(args.ipv6, args.per_range)
            ips += v6
            sources.append(f"{args.ipv6} ({len(v6)} 个, 每段抽{args.per_range})")
        except FileNotFoundError:
            print(f"⚠️ {args.ipv6} 不存在，跳过 IPv6")

    if not ips:
        print("❌ 没有找到任何 IP 列表文件！")
        print(f"   请确保 {DEFAULT_IPV4} 或 {DEFAULT_IPV6} 存在，或通过 -4/-6 指定")
        sys.exit(1)

    print(f"📋 已加载: {', '.join(sources)}，共 {len(ips)} 个 IP\n")

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

    print(f"\n📁 输出文件 -> {args.out_dir}/ :")
    write_outputs(results, args.top, args.out_dir)


if __name__ == "__main__":
    main()
