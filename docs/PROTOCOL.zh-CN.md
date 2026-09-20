# USART1 二进制链路协议 — v1

[English](PROTOCOL.md) · **中文**

只在 [`App/protocol/protocol.h`](../App/protocol/protocol.h) 中定义一次。PC 侧镜像是
[`pc_monitor/protocol.py`](../pc_monitor/protocol.py)，只要两者不一致
`pc_monitor/tests/test_protocol_vectors.py` 就会失败，因此本文档同时描述两者。

这不是文本协议。这条链路上任何地方都不存在 `printf("%d,%d\r\n")`。

## 成帧

所有整数均为小端序。没有带内转义 —— 接收方通过扫描魔数对来重新同步，这是安全的，
因为在计算 CRC 位置之前会先校验声明的长度。

| 偏移 | 大小 | 字段 | 说明 |
| --- | --- | --- | --- |
| 0 | 1 | `magic0` | `0xA5` |
| 1 | 1 | `magic1` | `0x5A` |
| 2 | 1 | `version` | `0x02`；不匹配的并非一帧，解析器会跳过它 |
| 3 | 1 | `type` | `pkt_type_t` |
| 4 | 2 | `sequence` | 按方向的计数器，在 65535 处回绕，从 0 开始 |
| 6 | 2 | `length` | 负载字节数，`0 … 64` |
| 8 | 4 | `device_ts_ms` | 构建该帧那一刻的 `HAL_GetTick()` |
| 12 | N | `payload` | |
| 12+N | 2 | `crc16` | 对字节 `0 … 11+N` 计算的 CRC-16/CCITT-FALSE |

帧长 = `14 + N`。最大帧 = 78 字节。

### CRC

`width 16 · poly 0x1021 · init 0xFFFF · refin false · refout false · xorout 0x0000`
ASCII `123456789` 的校验值是 **`0x29B1`**。刻意实现为逐位循环而非查表：
约 3.6 kB/s 无需查表来抵偿 512 字节 flash 的开销。

### 接收方状态

`pkt_parse()` 返回 `PKT_OK`、`PKT_NEED_MORE`、`PKT_ERR_CRC`、
`PKT_ERR_VERSION`、`PKT_ERR_LENGTH`，并报告 `consumed` —— 调用方在重试之前可以丢弃的
输入字节数。

* 长度错误或版本未知会越过伪魔数触发一次**内部重新扫描**；调用方看到的是
  `PKT_NEED_MORE` 或稍后的有效帧，绝不会卡死。
* CRC 失败恰好消耗 2 个魔数字节，从而保证能够向前推进。
* 单个末尾的 `0xA5` 会被**保留**，因为它可能是一半魔数、尚未接收完整。
  由 `test_protocol.c` 验证。

## 包类型

### 设备 → 上位机

| 类型 | Id | 用途 |
| --- | --- | --- |
| `PKT_HELLO` | `0x01` | 启动时给出身份与能力位 |
| `PKT_STATUS` | `0x02` | 诊断转储，2 Hz |
| `PKT_ECG_BATCH` | `0x10` | 1 kHz 记录，每帧 20 个采样，50 帧/秒 |
| `PKT_TEMP_STATUS` | `0x11` | 温度视图，2 Hz |
| `PKT_RTC_RESPONSE` | `0x20` | 对 `GET_RTC` 的应答，`SET_RTC` 成功后也会发送 |
| `PKT_PONG` | `0x31` | 回显 `PING` 的负载 |
| `PKT_ACK` | `0x40` | 接受了上位机命令 |
| `PKT_NACK` | `0x41` | 拒绝了上位机命令，携带原因 |

### 上位机 → 设备

| 类型 | Id | 负载 |
| --- | --- | --- |
| `PKT_START_STREAM` | `0x80` | 无 |
| `PKT_STOP_STREAM` | `0x81` | 无 |
| `PKT_SET_RTC` | `0x82` | 7 字节日历 |
| `PKT_GET_RTC` | `0x83` | 无 |
| `PKT_SET_CONFIG` | `0x84` | 工频陷波选择与报警区间 |
| `PKT_PING` | `0x90` | 4 字节不透明令牌，回显 |

`START_STREAM` / `STOP_STREAM` 控制的是**上报**，而不是 ADC。转换器、定时器与 DMA
从启动起就一直运行，因此不存在硬件启停状态机；参见 [ARCHITECTURE.zh-CN.md](ARCHITECTURE.zh-CN.md)。

## 负载布局

### `ECG_BATCH`（记录）

`n = sample_count`，`1 … 20`。负载大小 = `7 + 2n + 8`。

| 偏移 | 大小 | 字段 |
| --- | --- | --- |
| 0 | 1 | `sample_count` |
| 1 | 2 | `sample_period_us` = 1000 |
| 3 | 4 | `first_sample_index` —— 本批次第一个 ECG 采样的绝对索引 |
| 7 | 2n | `ecg_raw[]` —— **RAW 12 位 ADC 码值**，未滤波 |
| 7+2n | 2 | `temp_raw` —— 最新的温度 ADC 码值 |
| 9+2n | 2 | `temp_centi` (i16) —— 仅在标志位允许时才有意义 |
| 11+2n | 1 | `hr_bpm`，无效时为 0 |
| 12+2n | 1 | `hr_state` |
| 13+2n | 2 | `flags` |

正是 `first_sample_index` 让 PC 能够重建出精确的 1000 Hz 时间轴并检测出缺口：
下一帧的索引必须是 `first + n`。这就是仅凭记录本身发现被丢弃数据块的方式，
而不是去信任某个状态计数器。

当 `n = 20`：负载 55，帧 **69 字节**。

`flags` 是完整的 `u16`，全部十六个位都会到达上位机。它过去少了一个字节：
`ECGP_TAIL` 是手工数出来的字面量 `7U`，而上方的字段表需要 `8`；又因为 `pkt_build()`
用同样偏短的长度计算 CRC，帧校验能通过，而 `status_flags_t` 的位 8…15
根本没有被发送出去。如今该常量是推导出来的（`ECGP_TAIL = ECGT_FLAGS + 2`），
在 `protocol.h` 与 `pc_monitor/protocol.py` 中都是如此，并且
`tests/host/test_protocol.c::test_ecg_batch_tail` 与
`pc_monitor/tests/test_protocol_vectors.py` 把这个 69 字节的帧钉住。
正是这次修正使协议版本成为 `0x02`。

### `STATUS`（43 字节负载，57 字节帧）

偏移量命名为 `STP_*`：`adc_running`、`dma_blocks`、`dma_dropped`、
`ecg_samples`、`temp_valid`、`temp_centi`、`hr_bpm`、`hr_state`、
`oled_present`、`oled_addr`、`rtc_valid`、`uart_tx`、`uart_rx`、
`uart_crc_err`、`proto_err`、`flags`、`uptime_s`。

这与 OLED STATUS 页显示的是同一组计数器，因此台架上的观察与 PC 上的观察不可能不一致。

### `HELLO`（12 字节）

固件版本三元组、协议版本、`sample_rate_hz`、`batch_max_samples`、`adc_bits`、
能力位掩码：

| 位 | 名称 | 清零时的含义 |
| --- | --- | --- |
| 0 | `CAP_TEMP_CALIBRATED` | 温度为 `UNCALIBRATED`，不产生度数 |
| 1 | `CAP_OLED_PRESENT` | 没有显示屏应答 I2C 扫描 |
| 2 | `CAP_LEAD_HW_DETECT` | **不存在导联脱落硬件**，因此 `lead_state` 保持 `UNKNOWN` |
| 3 | `CAP_PROBE_HW_DETECT` | 探头故障仅来自 ADC 量程启发式判断 |
| 4 | `CAP_FRONTEND_VERIFIED` | 模拟前端增益/偏置只是假设 |
| 5 | `CAP_RTC_BATTERY_BACKED` | VBAT 保持尚未得到证实 |

上位机不得渲染它未被给予的能力位，并且在硬件另有说明之前，默认值全部为清零。

### `TEMP_STATUS`（8 字节）

`temp_raw`、`temp_mv`、`temp_centi`、`temp_state`。以 2 Hz 独立于 ECG 流发送，
因此停滞的 ECG 批次不会遮蔽温度视图。

### 日历（`SET_RTC`、`RTC_RESPONSE`）

`year:u16, month, day, hour, minute, second` —— 7 字节，用日历字段而非纪元时间，
从而把人类可读的意图放在链路上。`RTC_RESPONSE` 追加 `epoch:u32`。
设备校验每一个字段，包括每月天数与闰年，并回答 `NACK_BAD_VALUE` 而不改动时钟。
固件在内部转换为纪元秒。

### `SET_CONFIG`（7 字节）

`notch:u8`（0 = 50 Hz，1 = 60 Hz，2 = 关闭）、`hr_low`、`hr_high`（bpm），
以及以百分之一 °C 表示的 `temp_low:16` 与 `temp_high:16`。

### `ACK` / `NACK`

`acked_type:u8`、`acked_sequence:u16`，而对 `NACK` 还有一个来自 `nack_reason_t` 的
`reason:u8`。

## 状态标志字

| 位 | 名称 |
| --- | --- |
| 0–2 | `lead_state` —— `UNKNOWN`/`CONNECTED`/`DISCONNECTED`/`SIGNAL_POOR` |
| 3–5 | `temp_state` —— `OK`/`LOW`/`HIGH`/`PROBE_FAULT`/`UNCALIBRATED` |
| 6 | `hr_valid` |
| 7 | `recording` |
| 8 | `oled_present` |
| 9 | `adc_running` |
| 10 | `rtc_valid` |
| 11 | `dma_dropped` —— 自上次 `STATUS` 以来保持 |
| 12–13 | 生效的工频陷波 |
| 14 | `temp_uncalibrated` |

`lead_state` 有意区分 `SIGNAL_POOR` 与 `LEAD_DISCONNECTED`。
看到波形平直、输入触顶或噪声过大的软件会报告
`SIGNAL_POOR`；它绝不能把这称作电极脱落，因为没有注入电流或阻抗测量硬件，
就没有任何东西在测量电极。当 `CAP_LEAD_HW_DETECT` 清零时，诚实的默认值是 `LEAD_UNKNOWN`。

## 带宽

在 230400 波特、8N1 下 → 每字节 10 位 → 线路容量为 **23040 byte/s**。

| 包 | 帧长 | 速率 | Byte/s |
| --- | --- | --- | --- |
| `ECG_BATCH` (n=20) | 69 | 50/s | 3450 |
| `STATUS` | 57 | 2/s | 114 |
| `TEMP_STATUS` | 22 | 2/s | 44 |
| **稳态总计** | | | **3608** |

**利用率 15.7 %** —— 6.4× 的余量。最坏情况加上上位机命令和偶尔的
`ACK`/`RTC_RESPONSE` 突发，再多出不超过 4 %。

每秒采样预算：1000 个 ECG 码值 = 在 3450 byte/s 的 `ECG_BATCH` 帧之中有
2000 byte/s 的原始负载，即**每个采样 1.45 字节的开销**。

阻塞时间预算：一个 69 字节的帧在 `HAL_UART_Transmit` 内耗时
`69 × 10 / 230400` = **3.0 ms**。在 50 帧/秒下，主循环约 15.0 % 的时间用于发送。
ADC 通路不受影响，因为 TIM3 触发 ADC，DMA 在硬件中搬运结果；512 字节的双块在完成中断之间
给出 256 ms 的余量，而最坏情况是数毫秒的停滞。
每次发送都从 `App_Loop()` 发起，绝不从 ISR 发起。

如果 2.95 ms 最终被证明太粗糙，升级方案是通过 DMA1 做 TX（USART1_TX 用通道 6/7），
这是一次 CubeMX 修订，刻意暂不进行。

## 序号与丢失

序号是按方向、且与类型无关的；它们的存在是为了让 PC 能说出"这个流的 4711 号包从未到达"，
而不是为了排序 —— 该链路是全双工点对点，且按序到达。权威的丢失度量
是 `ECG_BATCH` 中 `first_sample_index` 的连续性，因为这也能捕捉到设备丢弃自身 DMA 块的
情形，而任何序号都揭示不了那种情况。

## 版本

`version` 是单字节，针对的是成帧，而不是负载模式。新增包类型向后兼容：
不认识某个类型的接收方会按 `length` 跳过它。修改布局意味着同时提升
`PROTOCOL_VERSION` **以及** `pc_monitor/protocol.py` 的 `PROTOCOL_VERSION`，
如果只动一侧，向量测试就会失败。
