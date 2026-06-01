# Cloudflare IP Optimizer

批量测试 Cloudflare Edge IP 的 **HTTPS 延迟**和 colo，筛选最优 IP（支持 IPv4 + IPv6）。

## 原理

通过 `curl --resolve` 向指定 IP 发起 HTTPS 请求：

```
https://cdnjs.cloudflare.com/cdn-cgi/trace
```

解析返回的 `colo`（数据中心代码，如 NRT/ICN/HKG/SJC）和 `loc`（地区），同时精确测量 **HTTPS 请求延迟（含 TLS 握手 + 数据传输）**。

## 依赖

- Python 3.8+
- curl（系统自带，需支持 IPv6）

## 使用方法

### 1. 自动生成 IP 并测试

```bash
python cf_optimizer.py --generate
```

内置 IP 段：

| 类型 | CIDR |
|------|------|
| IPv4 | `104.16.0.0/12` |
| IPv4 | `104.64.0.0/10` |
| IPv6 | `2606:4700::/44` |
| IPv6 | `2400:cb00::/32` |

### 2. 使用自定义 IP 列表

```bash
# 编辑 ips.txt，每行一个 IP（支持 # 注释）
python cf_optimizer.py -f ips.txt
```

### 3. 常用参数

```bash
python cf_optimizer.py --generate -c 50 -t 3 -n 20 --export best_ips.txt
```

| 参数 | 说明 | 默认 |
|------|------|------|
| `-f` / `--file` | IP 列表文件 | ips.txt |
| `-c` / `--concurrency` | 并发数量 | 20 |
| `-t` / `--timeout` | 超时(秒) | 5 |
| `-n` / `--top` | 显示最优 N 个 | 30 |
| `--generate` | 从内置 IPv4/IPv6 段生成 | - |
| `--per-range` | 每段取样数 | 3 |
| `--export` | 导出最优 IP 到文件 | - |
| `--sort-by-colo` | 按数据中心分组输出 | - |

### 4. 示例输出

```
🚀 开始测试 48 个 IP（并发=20 超时=5.0s）...
✅ 测试完成，耗时 4.2s

======================================================================
IP                                          延迟    Colo    Loc    状态码
----------------------------------------------------------------------
104.17.0.1                                12.3ms    NRT     JP     200
104.16.0.1                                15.7ms    ICN     KR     200
2606:4700::1                              18.2ms    HKG     HK     200
104.18.0.1                                23.1ms    SJC     US     200
...

📊 各 Colo 最低延迟:
   NRT  最低=12.3ms  平均=18.7ms  (样本=8)
   ICN  最低=15.7ms  平均=22.1ms  (样本=5)
   HKG  最低=18.2ms  平均=25.4ms  (样本=6)
```

## 进阶用法

### 仅测试 IPv6

```bash
# ips.txt 只放 IPv6 地址
python cf_optimizer.py -f ips_v6.txt -n 10
```

### 导出并按 colo 分组

```bash
python cf_optimizer.py --generate --sort-by-colo --export best_by_colo.txt
```

## License

MIT
