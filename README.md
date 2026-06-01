# Cloudflare IP Optimizer

批量测试 Cloudflare Edge IP 的 **HTTPS 延迟**和 colo，筛选最优 IP（支持 IPv4 + IPv6）。

## 原理

通过 `curl --resolve` 向指定 IP 发起 HTTPS 请求：

```
https://cdnjs.cloudflare.com/cdn-cgi/trace
```

解析返回的 `colo`（数据中心代码，如 NRT/ICN/HKG/SJC）和 `loc`（地区），同时精确测量 **HTTPS 请求延迟（含 TLS 握手 + 数据传输）**。

运行后自动生成 4 个输出文件。

## 依赖

- Python 3.8+
- curl（系统自带，需支持 IPv6）

## 文件说明

| 文件 | 说明 |
|------|------|
| `cf_optimizer.py` | 核心脚本 |
| `ipv4.txt` | IPv4 地址列表（单 IP 或 CIDR 格式） |
| `ipv6.txt` | IPv6 地址列表（单 IP 或 CIDR 格式） |
| `requirements.txt` | 依赖说明 |

程序不自带 IP —— 全部从 `ipv4.txt` 和 `ipv6.txt` 读取。

## 输出文件

运行后自动生成 4 个文件：

| 文件 | 内容 |
|------|------|
| `results_full.txt` | 完整结果表（所有 IP，含 colo / 延迟 / 状态码） |
| `best_ipv4.txt` | 最优 IPv4（每行一个 IP，按延迟升序） |
| `best_ipv6.txt` | 最优 IPv6（每行一个 IP，按延迟升序） |
| `best_all.txt` | 最优混合（每行一个 IP，按延迟升序） |

## 使用方法

### 1. 直接运行（默认加载 ipv4.txt + ipv6.txt）

```bash
python cf_optimizer.py
```

### 2. 仅测试 IPv4

```bash
python cf_optimizer.py --no-ipv6
```

### 3. 仅测试 IPv6

```bash
python cf_optimizer.py --no-ipv4
```

### 4. 常用参数

```bash
python cf_optimizer.py -c 50 -t 3 -n 50 -o ./output
```

| 参数 | 说明 | 默认 |
|------|------|------|
| `-4` / `--ipv4` | IPv4 列表文件 | `ipv4.txt` |
| `-6` / `--ipv6` | IPv6 列表文件 | `ipv6.txt` |
| `--no-ipv4` | 跳过 IPv4 | - |
| `--no-ipv6` | 跳过 IPv6 | - |
| `-c` / `--concurrency` | 并发数量 | 20 |
| `-t` / `--timeout` | 超时(秒) | 5 |
| `-n` / `--top` | 最优 IP 数量 | 30 |
| `-o` / `--out-dir` | 输出目录 | `.` |
| `--sort-by-colo` | 按数据中心分组输出 | - |

### 5. 示例输出

```
📋 已加载: ipv4.txt (7497 个), ipv6.txt (633 个)，共 8130 个 IP

🚀 开始测试 8130 个 IP（并发=20 超时=5.0s）...
✅ 测试完成，耗时 124.3s

======================================================================
IP                                          延迟    Colo    Loc    状态码
----------------------------------------------------------------------
104.17.0.1                                12.3ms    NRT     JP     200
104.16.0.1                                15.7ms    ICN     KR     200
2606:4700:90c5::1                          18.2ms    HKG     HK     200
...

📊 各 Colo 最低延迟:
   NRT  最低=12.3ms  平均=18.7ms  (样本=248)
   ICN  最低=15.7ms  平均=22.1ms  (样本=195)
   HKG  最低=18.2ms  平均=25.4ms  (样本=210)

📁 输出文件:
📄 results_full.txt  (7980 成功 + 150 失败)
📄 best_ipv4.txt     (30 个 IPv4)
📄 best_ipv6.txt     (30 个 IPv6)
📄 best_all.txt      (30 个混合)
```

## 自定义 IP 列表

编辑 `ipv4.txt` 或 `ipv6.txt`，支持以下格式：

```
# 单 IP
1.1.1.1
8.8.8.8

# CIDR（自动取该段第一个可用 IP）
104.16.0.0/12
2606:4700::/44
```

## License

MIT
