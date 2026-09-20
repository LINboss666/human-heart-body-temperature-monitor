# 阶段 1 — 独立审查交接包

[English](PHASE1_REVIEW_HANDOFF.md) · **中文**

| | |
| --- | --- |
| 仓库 | https://github.com/LINboss666/human-heart-body-temperature-monitor (public) |
| 待审分支 | `phase1/full-system` |
| 代码 HEAD | `9e3f435` — 请审查直到并包括这个提交的全部内容；本文档提交在它之上，且不改动任何代码 |
| 阶段 0 基线 | 标签 `v0.1-baseline` → 提交 `eb8a795`（**不要移动**） |
| 自基线以来的变化 | 116 个文件，+24318 / −248 |
| 固件构建 | `0 Error(s), 0 Warning(s)` — ARMCC V5.06 update5，`-O3`，警告等级 2，**未抑制任何警告** |
| 占用 | 已被 `phase1/review-fixes` 取代：`Code=38152 RO=3076 RW=380 ZI=7524` → flash **63.5 %**，RAM **38.6 %** |
| 宿主 C 测试 | 6 个二进制，**1004 条断言，0 失败**（本次审查时是 901） |
| Python 测试 | **256 个用例，0 失败**（含无头 GUI；这里是 251） |
| 硬件已验证 | **无。** 见 §5 |

> **历史存档。** 这是*进入* `phase1/full-system` 审查的那份交接包。上面的占用与测试
> 计数已不再是当前值，而 §4 关于锚点的描述也早于其后两次改动：DR1 字如今是一个提交标记，
> 围绕负载先写空、再写有效，而不再是一个常量魔数；为它提供数据的那次计数器读取之前，也加进了一段
> 有界的 RSF 重新获取。当前状态见
> [`PHASE1_REVIEW_FIX_HANDOFF.md`](PHASE1_REVIEW_FIX_HANDOFF.zh-CN.md) 与
> [`PHASE1_FINAL_HANDOFF.md`](PHASE1_FINAL_HANDOFF.zh-CN.md)。

先读 [`README.md`](../README.zh-CN.md) 了解本项目声称做到什么，再读
[`docs/COURSE_REQUIREMENTS.md`](COURSE_REQUIREMENTS.zh-CN.md) 了解逐条需求的状态表。
本文档只讲*该去哪里找问题*。

---

## 1. 一句话的诚实立场

本仓库里的每一个算法都已在宿主编译器上被执行并被断言过；**没有一行代码曾在目标芯片上跑过**。
任何被描述为 `HOST VERIFIED` 的东西，都是关于算术的主张，不是关于测量的主张。

## 2. 对阶段 0 基本规则的两处偏离 — 都是故意的

阶段 0 说过：绝不编辑 `USER CODE` 区域之外的 CubeMX 生成代码，也绝不改动 `.ioc`。

**(a) `Core/Src/rtc.c` 里有一行在 `USER CODE` 区域之外。**

```c
hrtc.Init.OutPut = RTC_OUTPUTSOURCE_NONE;
```

CubeMX 原本生成的是 `RTC_OUTPUTSOURCE_ALARM`，并且路由到了 tamper 引脚上；这不是任何人想要的，
也无法从 `USER CODE` 块内部挽回，因为 `HAL_RTC_Init()` 会消费掉这个结构体。对应的改动
**同样做进了 `.ioc` 里** — `VP_RTC_No_RTC_Output.Mode=RTC_OUT_NO` — 所以重新生成时会复现它，
两边保持一致。提交 `488521b`。

核验方法：在 CubeMX 6.17.0 配 FW_F1 V1.8.7 下重新生成并做 diff；预期结果是
`Core/Src/rtc.c` 没有差异。

**(b) `Mcu.PinsNb` 从 18 → 19。** 那是 CubeMX 为一个新增的*虚拟*引脚重新编号，
外加 `Mcu.PinNN` 这些键整体位移。**没有增加或改动任何物理引脚。**
[`PINMAP.md`](PINMAP.zh-CN.md) 仍然是权威文件；请拿它对照
`.ioc` → `Mcu.Pin*` 以及 `*.ioc` 的信号赋值。

`Core/` 下其余所有内容在 `USER CODE` 区域之外都未被触碰。`main.c` 增加了三行
（`#include "app.h"`、`App_Init()`、`App_Loop()`）；`rtc.c` 在 `RTC_Init 0` 和
`RTC_Init 2` 内部增加了 preserve/restore 调用。

## 3. 有意思的 bug 大概藏在这些地方

按"如果我搞错了会造成多大损害"排序。

**(1) `App/protocol/` 和 `pc_monitor/protocol.py` — 线上契约。**
已经找到过一次：`ECGP_TAIL` 是手工数出来的 `7U`，而字段表需要的是 `8`，于是
`pkt_put_u16(&p[off + ECGT_FLAGS], flags)` 在声明长度之外多写了一字节，而 CRC —
在同一段短长度上算出来的 — 居然照样通过。`status_flags_t` 有一半从未到达宿主端。
在 `a2573ee` 中修好；这个常量现在两侧都是*推导*出来的。去找其他把同一个尺寸写两遍的地方。

**(2) `App/ecg/ecg_signal.c` — 整数定点。**
整条链路是微分 → 平方 → 泄漏积分器 → 阈值 → 不应期，全部用整数。已经有两类 bug 在这里咬过一口，
而且都是静默的：

* 一旦 `|acc| < 2^k`，`acc -= acc >> k` 就会**停摆**（一个很小正商的向下取整是 0）。它先造成了
  永久性的峰值保持，随后又把能量下限冻在 779。两处都是靠 `ema_step()` 修掉的，它用的是对幅值
  向上取整的衰减。在任何累加器里 grep `>>`，并问一句它会不会停摆。
* 对负值做带符号 `>>` 是实现自定义行为。除以 `2^k` 则不是。剩下的那些移位按理都作用在非负操作数上 —
  请核查这一点。

一个 Q14 双二阶在被积分器 + boxcar 替换掉之前，实测到了在完全平坦的输入上稳定输出
**339 个计数**：半个 LSB 的舍入项被一个在 DC 处接近零的分母放大了 ~1024×。
如果你看到一个分母很小的滤波器，去找舍入偏置，而不是系数错误。

**(3) 采样时序。** TIM3 TRGO → ADC 外部触发 → DMA1 循环模式。没有使能任何定时器 ISR。
半满/全满回调只置一个位掩码。值得攻击的那个问题是：`App_Loop()` 会跟不上 1 kHz 吗？
环形缓冲是 512 个半字 = 256 ms 的余量，但 UART 发送是**阻塞**的，一个 69 字节的帧要 3.0 ms；
50/s 就占掉这个循环的 15 %。再把 OLED（阻塞式 I2C，400 kHz）放进同一个循环里，
最坏路径请你自己算一遍。

**(4) `App/rtc_service/rtc_service.c` — 与 CubeMX 的时钟复位作斗争。**
CubeMX 在每次启动时无条件调用 `HAL_RTC_SetTime/SetDate` 把时间设成 1970-01-01。
由于在 `RTC_Init 0` 处 `hrtc.Instance` 仍然是 `NULL`，那里没法通过 HAL 读寄存器，所以该服务直接读
原始备份域计数器，并存一个**锚点**：BKP_DR1 魔数 `0x2B1C`，DR2/DR3 是 epoch、DR4/DR5 是计数器，
每个都拆成两个 16 位半字（F1 的 BKP 寄存器是 16 位，不是 32 位）。流逝的时间随后由计数器差值重建，
而不是被假定为零。`docs/PHASE1_REVIEW_FIX_HANDOFF.md` 取代了本段早先那种只镜像 epoch 的描述。

**(5) `App/display/oled_bus.c` — 三套控制器配置，全部未验证。**
SSD1306 / SH1106 / CH1116 的初始化表，以及 0x3C/0x3D 地址扫描。我们不知道会寄到哪一块屏。
上游自己的移植规则禁止猜测，而一次 I2C 应答只能证明*有个东西*在答。

**(6) `App/ui/ui_app.c` — KK_UI 页面表。**
约定是 `routes[0] = page ID`，而在 `MENU_PAGE` 里 ref 字段是一个页面 **ID**，
在其余每一行里它是一个表 **索引**。这种不对称是从 KK_UI 继承来的，也是最可能给出
一个"错但看着合理"的跳转目标的地方。

**(7) `Core/Src/main.c` 的 `Error_Handler()`。**
与模板一致：一个开着中断的无限循环。对于一个绑在人身上的设备来说这是不是对的，
是一个我还没回答的设计问题。

## 4. 有意*没有*实现的东西

* **温度是 `UNCALIBRATED`。** `TEMP_SENSOR_MODEL` 默认为 `TEMP_MODEL_UNCALIBRATED`，
  所以 `valid=false`，UI 显示 `--.- °C`。前端是另一位组员的设计，不在这个仓库里。
  有两个宿主测试二进制覆盖了两个分支（未标定的，以及用
  `-DTEMP_SENSOR_MODEL=TEMP_MODEL_LINEAR_MV` 注入线性模型的那条），所以标定钩子是
  *已知可用*，不是假定可用。
* **没有导线脱落检测。** 没有这样的硬件。固件上报的是 `SIGNAL_POOR` 或 `LEAD_UNKNOWN`，
  从不声称某个电极断开了。
* 对 `LEAD_HW_DETECT`、`PROBE_HW_DETECT` 和 `RTC_BATTERY_BACKED`，**能力位保持清零**，
  这样 PC 端就不可能去渲染一块板子上没有的功能。
* `ECG_FRONTEND_PARAMS_VERIFIED` 是 `0`，`ECG_FRONTEND_GAIN/OFFSET_MV` 是占位值。
  每一份导出里的 `ecg_pin_mv` 都是一个*引脚*电压。

## 5. 验证：每条主张各自靠什么支撑

| 主张 | 证据 | 强度 |
| --- | --- | --- |
| 干净编译 | `UV4 -j0 -b`，退出码 0，日志结尾是 `0 Error(s), 0 Warning(s)` | 强 |
| 装得进这颗器件 | 链接器 map 的尺寸，在 64 KB/20 KB 上做算术 | 强 |
| 成帧 + CRC 在 C 与 Python 之间一致 | 21 个黄金帧，**由出厂的那份 C 生成**（`tools/gen_protocol_vectors.py` → `tests/host/protocol_vectors.json`），并在 pytest 里回放，含一项过期快照重编译检查 | 强，仅限宿主 |
| 日历正确 | C：1970→2099 逐小时往返。Python：独立对照 `datetime`。外加把 `pc_monitor/rtc.py` 作为第三个实现 | 强 |
| 检测器能找到心跳 | 带 0.3 Hz 漂移 + 50 Hz + 噪声的合成心跳：50→49、60→60、72→72、95→95、120→120、150→150 bpm | **对真实信号而言很弱** |
| 字体可解码 | 拿厂商未经修改的 `kk_oled_font.c` 在宿主上跑我们自己的数组 | 对格式来说是强项，对外观保持沉默 |
| GUI 能用 | `QT_QPA_PLATFORM=offscreen`，点击真实控件，记录并导出了 >1000 行 | 对线路接通是强项，**对外观是零** |
| 任何测量 | 无 | **Unverified** |
| 1 kHz 在 UI+UART 负载下能守住 | 无 | **Unverified** |
| RTC 晶振起振 / VBAT 保持 | 无 | **Unverified** |
| 屏上出现过任何一个像素 | 无 | **Unverified** |

明确**没有**主张的东西：±2 bpm 精度、医疗级信号质量、任何诊断能力，
或者模拟前端是正确的。

## 6. 我自己已经看到的已知弱点

1. **不完整的批次永远不会被冲刷出去。** `send_ecg_batch()` 只在攒满 20 个样本时才触发，
   所以中途来一次 `STOP_STREAM` 就可能留下最多 19 个样本没发出去。样本索引的跳变会暴露它；
   没有任何东西能找回它们。
2. **主循环里的阻塞 UART 和阻塞 I2C。** 除了 §3(3) 里那笔算术之外，没有做过任何优先级分析。
3. **`ui_fonts.c` 是一个 5×7 ASCII 数组，被别名进三个字体槽位。** 对 128×64 的演示来说够用，
   而且 `tools/gen_oled_fonts.py` 就是用来做真字体的，但没有 CJK 字形覆盖，
   也没有更大的正文字号。
4. **3 s 的 `ACQUIRING` 窗口是一个固件常量，并由演示设备镜像。** 如果真实的稳定行为不同，
   演示就是在替它撒谎。
5. **`describe_epoch()` / `looks_like_demo()` 是启发式的**，后者还可能在一块跑着 0.0.0 固件的
   真实板子上被触发。它只被允许*增加*一条警告，绝不允许删除一条。
6. **XLSX 图表只是被 `openpyxl` 读回来过，从没在 Excel 里打开过。**
7. **自 RTC 修复以来没有跑过任何 `.ioc` 重新生成** — §2(a) 是我对它将会怎样的预测，
   不是一次观察结果。

## 7. 第三方代码

| | |
| --- | --- |
| KK_UI | 提交 `582c3442ecbc539c1c82a342676b5b2eda69eee0`，MIT — 收录于 `ThirdParty/kk_ui/` |
| KK_OLED | 提交 `f01831d63b1d426b629921edaba644732aa29223`，MIT — 收录于 `ThirdParty/kk_oled/` |
| 被改动的文件 | **共四处。** KK_UI：`include/kk_ui_config.h` → 改为阻塞式刷新。KK_OLED：`driver/kk_oled_driver.{c,h}` → 地址、列偏移和初始化表现在由 `oled_bus` 提供。完整 diff 与理由：[`UPSTREAM.md`](UPSTREAM.zh-CN.md) |
| 字体 | 上游字形*服务*的输出不在 MIT 覆盖范围内，所以字体数组是在本仓库内由 `tools/gen_oled_fonts.py` 编写的。不分发任何上游字形字节。 |

许可证是在收录进仓库**之前**读过的；两者都允许这样的再分发。

## 8. 如何复现这套验证

```
# 1. firmware (Windows + Keil; the log must end "0 Error(s), 0 Warning(s)")
"C:\Keil_v5\UV4\UV4.exe" -j0 -b MDK-ARM/"Human Heart and Body Temperature Monitor".uvprojx -o build.log

# 2. Python environment -- no Keil licence needed for anything below this line
cd pc_monitor
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt

# 3. host C algorithms (run from the repo root; 1004 assertions, 6 binaries)
cd .. && pc_monitor/.venv/Scripts/python.exe tools/run_host_tests.py

# 4. golden frames: regenerates tests/host/protocol_vectors.json from the real C.
#    A committed-vs-regenerated diff is asserted by
#    pc_monitor/tests/test_protocol_vectors.py, which recompiles this itself.
pc_monitor/.venv/Scripts/python.exe tools/gen_protocol_vectors.py

# 5. PC tool, including the headless GUI group (251 cases)
QT_QPA_PLATFORM=offscreen pc_monitor/.venv/Scripts/python.exe -m pytest pc_monitor/tests -q
```

`tools/add_keil_sources.py` 是**幂等**的，并且在任何一次 CubeMX 重新生成之后都必须重跑：
`.uvprojx` 归 CubeMX 所有，它会丢掉手工添加的分组。

随手记：`tools/_restamp_golden.py` 存在的唯一理由，是让 `pc_monitor/protocol.py` 里手写的
黄金向量十六进制变得可复现而不是神秘。它只跑过一次，是在 `PROTOCOL_VERSION` 从 1 → 2 的时候。

## 9. 怎样才能让这次审查最有价值

请按这个顺序发起攻击：

1. **`ecg_signal.c` 里的整数信号链** — 停摆和舍入偏置这两类 bug 对编译是不可见的，
   而每一类都已经漏掉过一次了。
2. **任何把一个尺寸写了两遍的地方** — 也就是 `ECGP_TAIL` 那一类 bug。
   也请检查 `TEMPP_SIZE 8U /* 7 used */` 和 `RTCP_CAL_SIZE + RTC_RESPONSE_EXTRA`。
3. **1 kHz / 阻塞 I/O 的预算能不能扛住一次完整重绘**，因为测量记录和显示在争同一个循环。
4. **`rtc_service_preserve/restore` 的顺序**，对照一次真实的 `HAL_RTC_Init()`。
5. **任何可能让一句假的、看着像临床结论的话到达屏幕或电子表格的东西。**
   即便算术是对的，也要把一个编造出来的数字当作缺陷。

不要把时间花在代码风格、计划里缺的功能，或者硬件未测试这个事实上 — 这些是已知的，
并且已在 [`HARDWARE_TEST_PLAN.md`](HARDWARE_TEST_PLAN.zh-CN.md) 里逐条列好。

---

## 10. 已遵守的约束

没有强推。`v0.1-baseline` 未移动，未删除任何历史。未合并进 `main`。
没有提交 GitHub token、Keil 许可证文件或构建日志（`.gitignore` 排除了 `*.log`、`*.htm`
以及整个 Keil 输出目录，Arm 许可证序列号就在那里）。Keil 的内存区域和器件编号未改动；
没有全局抑制警告。每一处 `.ioc` 可见的改动都有一次对应的 `.ioc` 编辑。
提交的邮箱身份是 GitHub 的 noreply 地址，不是个人邮箱，因为这个仓库是公开的。

---

PHASE 1 READY FOR GPT CODE REVIEW
