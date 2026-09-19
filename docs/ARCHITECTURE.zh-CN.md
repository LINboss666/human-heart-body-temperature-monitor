# 架构 — Phase 1

[English](ARCHITECTURE.md) · **中文**

无 RTOS、无堆、协作式超级循环，运行于 STM32F103C8T6（64 KB flash / 20 KB RAM）。

## 数据流

```
TIM3 (72 MHz, PSC 71, ARR 999)
  │  update event → TRGO, 1000 Hz, entirely in hardware
  ▼
ADC1 scan: rank1 = CH0/PA0 (ECG), rank2 = CH1/PA1 (temperature)
  │  each result → DR
  ▼
DMA1_Channel1, circular, half-word, memory increment   [1024 B ring = 2 blocks]
  │  half-transfer IRQ ─┐        full-transfer IRQ ─┐
  ▼                     ▼                           ▼
  ready mask 0x02       ready mask 0x01      (set flag + frame index only;
  │                                          nothing else happens in ISR)
  ▼
App_Loop()  ── acquisition_take_block() ──► 128 frames per block
  │
  ├─► ecg_signal_process(raw)
  │      ├── RAW      unfiltered code            → protocol, CSV, calibration hook
  │      ├── DISPLAY  ~0.62 Hz baseline removal → 20-tap comb (50 Hz null)
  │      └── QRS      5 Hz HP → MA(20) → derivative → square → 120 ms MWI
  │                └──► detect_qrs(): floor EMA, envelope, self-calibrating threshold
  ├─► ecg_hr_tick / ecg_hr_notify_beat  → median of last 5 RR → bpm + state
  ├─► temperature_feed(temp code)       → 250-sample average → raw/centi + state
  ├─► ui_app_push_waveform(display)     → 128 columns × min/max
  ├─► protocol_service_push_sample()    → ECG_BATCH every 20 samples
  ├─► buttons_scan()                    → KK_UI key bitmask + app-level events
  ├─► rtc_service_poll()                → epoch advance + 1 s hardware resync
  ├─► protocol_service_poll()           → RX parse, commands, periodic packets
  └─► ui_app_update()                   → KK_UI_Update + partial-area OLED flush
```

## 模块映射

| 路径 | 是否依赖 HAL？ | 职责 |
| --- | --- | --- |
| `App/app.c` | 是 | 超级循环、执行顺序、录制状态、诊断信息刷新 |
| `App/acquisition/` | 是 | 启动序列、DMA 环形缓冲、块交接、溢出丢弃计数 |
| `App/ecg/ecg_signal.c` | **否** | 滤波与 R 峰检测，纯整数 |
| `App/ecg/ecg_hr.c` | **否** | RR 历史、中位数、心率状态机 |
| `App/temperature/` | **否** | 抽取、探头启发式、校准钩子 |
| `App/rtc_service/rtc_calendar.c` | **否** | Epoch ↔ 日历 |
| `App/rtc_service/rtc_service.c` | 是 | RTC 访问、备份寄存器锚点（epoch ↔ 计数器），以事务方式写入 |
| `App/buttons/` | 是 | 消抖、事件、原始掩码 |
| `App/protocol/protocol.c`、`crc16.c` | **否** | 组帧、CRC、重新同步 |
| `App/protocol/protocol_service.c` | 是（仅 tick） | 批量包构建、命令分发 |
| `App/uart/uart_link.c` | 是 | 轮询式 RX 环形缓冲、阻塞式短发送 |
| `App/display/oled_bus.c` | 是（I2C 探测） | 控制器候选方案、地址扫描 |
| `App/ui/` | 是 | KK_UI 页面表、自定义波形页面、字模数据 |
| `ThirdParty/kk_ui`、`ThirdParty/kk_oled` | 仅驱动 | UI 库与图形核心 |

HAL 列中标为 **否** 的那四个模块，加上 `rtc_calendar` 与 `temperature`，正是被 `tests/host/`
在上位机上编译并执行的那几个，这就是这些算法拥有真实测试覆盖、而不仅仅是审阅式保证的原因。

## 代码实际遵守的规则

1. **任何地方都没有 `malloc`、`calloc`、`realloc` 或 `free`** —— 包括在随仓携带的库里
   也没有，已通过分别对两者的 grep 核查。
2. **信号链中不使用浮点。** 所有滤波、检测与温度运算都是整数；唯一的小数是被定点缩放过的
   整数（百分度、以 2 的幂移位表示的 Q14 系数）。
3. **中断服务程序只做置标志这一件事。** 不做滤波，不做 UART，不做 OLED，不做浮点。
4. **应用逻辑中没有 `HAL_Delay`。** 随仓携带 OLED 驱动里的那一处 `HAL_Delay(20)` 在采样链
   启动之前的开机阶段运行。
5. **生成文件只在 `USER CODE` 区域内修改。** `Core/Src/main.c` 与 `Core/Src/rtc.c` 中新增的
   全部八行都在这些区域内。
6. **`.ioc` 是配置的唯一权威来源。** 没有任何生成设置被手工改动过；RTC 输出变更与日历重置
   都来自 CubeMX。
7. **主循环中没有无界循环。** 排空块受块数限制，抽取 RX 受字节预算限制，OLED 刷新受脏区
   限制。
8. **任何东西都不会报告自己并不具备的能力。** 导联脱落硬件、探头硬件与 VBAT 保持能力在
   `HELLO` 中都声明为不存在。

## 为什么不用 RTOS

任务书要求不用，资源预算也允许不用，但诚实的理由更窄：这里恰好只有两项周期性活动
（1 kHz 采样产出、约 25 Hz UI）加上按需 I/O，而硬件已经用 DMA 做完了 1 kHz 那部分。
调度器会为任务栈和上下文状态额外增加 RAM，并且仍然需要同样的缓冲交接，换来的却是牺牲掉
本设计中唯一使它可审计的东西 —— 一个所有回调同样在其中运行的、单一可见的执行上下文。

## 已知的架构薄弱点

1. **`HAL_UART_Transmit` 是阻塞的**，一整个批量帧最长约 3 ms。它在主循环里，从不在中断里，
   而 128 ms 的 DMA 缓冲能吸收它 —— 但余量是由算术推导出来的，并非实测得出，真正是否成立
   要由 Stage G/M 在真实硬件上判定。逃生通道是把 TX 放到 DMA1 channel 6/7 上，这需要一次
   CubeMX 修订。
2. **`KK_UI_REFRESH_MODE` 是 BLOCKING**，代价是损失一个帧缓冲量级的并发性（不是内存 ——
   KK_OLED 的两块 1 KB 缓冲是硬编码的，仍然会被分配）。之所以这样选，是为了避免为一个仅关
   乎显示的好处去改 CubeMX。
3. **`HAL_GetTick()` 会回绕**，周期约 49.7 天。每一处间隔比较都写成
   `(uint32_t)(now - then)`，这是回绕安全的；但 `rtc_service_get_timestamp()` 会把经过的秒数
   加到存储的 epoch 上，如果期间没有发生过重新同步，跨越回绕时结果就会错；1 s 的重新同步在
   实践中堵住了这个口子。
4. **按设计假定单一执行上下文**，因此日后把 RX 移到中断里时，必须重新审视环形缓冲的
   `volatile` 纪律 —— 它目前是过度保险，而非严格必要。
5. **Keil 分组不归 CubeMX 所有。** 一次重新生成就会把它们丢掉，
   `tools/add_keil_sources.py` 必须再跑一遍。
