# 阶段 1 最终修复交接包

[English](PHASE1_FINAL_HANDOFF.md) · **中文**

上电调试之前的最后一次软件修正。只涉及三件事：任何计数器读取之前的 RTC 寄存器同步、
备份寄存器锚点在撕裂写之下的行为，以及温度探头的故障/恢复响应时间。没有功能开发，没有
普遍性清理，也没有对任何本来就能工作的东西做重新设计。

| | |
| --- | --- |
| 分支 | `phase1/final-fixes` → **已合并进 `main`**，在本分支的 GPT 审查被接受之后（见 §7 末尾）；没有 release 标签 |
| 起始提交 | `282713b`（`phase1/review-fixes` 的末端；那条分支未被改动） |
| 前序链 | `phase1/full-system` → `phase1/review-fixes` → 本分支 |
| 冻结基线 | 标签 `v0.1-baseline` → `eb8a795`（从未动过） |
| 改动文件 | 19 — 代码与测试 11 个（`App/` 下 8 个，`tests/host/` 下 3 个）外加 8 个文档文件 |
| 固件构建 | `0 Error(s), 0 Warning(s)`（一次完整的 `UV4 -j0 -r` 重新编译，ARMCC V5.06 u5，`-O3`，警告等级 2，什么都没屏蔽） |
| 占用 | `Code=38392 RO-data=3096 RW-data=380 ZI-data=7524` → flash 41868/65536 = **63.9 %**，RAM 7904/20480 = **38.6 %** |
| 宿主 C 测试 | 6 个二进制，**1050 条断言，0 失败**（原为 1004） |
| Python 测试 | **256 个用例，0 失败**，含无头 GUI |
| 黄金向量 | 重新生成，**没有 diff** → 线上格式未变 → `PROTOCOL_VERSION` 保持为 **2** |
| CubeMX | `.ioc` 未修改，没有重新生成，在已有的 `USER CODE` 区域之外没有任何手工编辑 |
| 硬件已验证 | **无。** 本轮没有改变这一点；§7 说明了这样做的代价 |

---

## 1. RTC 预读取同步

**这项指控，以及它是否成立。** 审查说代码在复位之后读取 `RTC_CNTH`/`RTC_CNTL`
时没有重新获取同步标志，所以它可能读到上一次会话的影子寄存器。**成立。** 根因是
结构性的，不是笔误：唯一能在 CubeMX 摧毁它之前读到计数器的地方是 `USER CODE BEGIN RTC_Init 0`
（`Core/Src/rtc.c:33`），它运行在 `MX_RTC_Init()` 给 `hrtc.Instance` 赋值*之前*，所以
`HAL_RTC_*` —— 包括 `HAL_RTC_WaitForSynchro()` —— 在那里根本用不了。真正执行了等待的
唯一地方是 `HAL_RTC_Init()`，也就是复位早已发生之后。

**STM32F1 实际需要什么。** 计数器住在备份域内的 RTC 内核里，在 NRST 期间、以及在
接有 VBAT 时 VDD 被移除期间，它一直在跑。复位打断的是 `CNTH`/`CNTL` 的 AHB 到 APB 的
呈现：那些是同步过的副本，而在 `RTC_CRL` 里的 `RSF` 重新置起之前，一次读取可能返回复位前
锁存的值。一个陈旧的计数器会让"供电断了多久"这个差值朝任一方向出错，**包括错到零**，
而这正是那种看起来像成功的失败。

**修复。** `App/rtc_service/rtc_service.c` 里的 `rtc_sync_before_read()` 在裸寄存器上
照做了 `HAL_RTC_WaitForSynchro()` 所做的事：

```c
CLEAR_BIT(RTC->CRL, RTC_FLAG_RSF);
while ((RTC->CRL & RTC_FLAG_RSF) == 0U) {
    if ((uint32_t)(HAL_GetTick() - started) > RTC_TIMEOUT_VALUE) return false;
}
```

* **有界。** `RTC_TIMEOUT_VALUE` 就是 HAL 自己的 1000 ms
  （`Drivers/STM32F1xx_HAL_Driver/Inc/stm32f1xx_hal_rtc.h:67`）。最坏情况下，启动晚
  一秒，并且时钟被报告为未设置。这条路径上不存在无界自旋。
* **显式，而非暗示。** 结果由 `bool s_counter_valid` 承载；计数器 `0`
  从不被当作"有效，并且流逝时间也恰好为零"，因为 `s_counter_at_boot` 只在同步完成时才采样：
  `s_counter_at_boot = s_counter_valid ? hw_counter_raw() : 0U;`
* **顺序被强制。** 解锁备份接口 → 重新获取 `RSF` → 读计数器 → 读
  锚点。`preserve()` 记录了中间那一步为什么不能挪。
* **没有被吞掉。** 这个失败是粘滞的（`s_sync_failed`），并作为
  `rtc_service_sync_failed()` 暴露出来，而 `App_Init()` 把它报告为 `DIAG_ERR_RTC_SYNC` (201) ——
  在 `diagnostics_init()` 之后，因为 `MX_RTC_Init()` 运行得更早，会把在那之前记下的东西 memset 掉。

**后果，而它正是有意为之的那个后果。** 没有同步过的计数器，流逝区间就是未知的，所以一个
存下来的 epoch 轻则陈旧几个小时，重则与本次启动毫无关系。`rtc_service_restore()` 因此拒绝
这次重建，并让时钟保持**无效**；CubeMX 的 2000-01-01 继续运行，UI 显示 `UNSET`，任何东西
都不会被当作时间戳呈现。`rtc_service_init()` 应用同一条规则
（`s_valid = s_anchor_available && s_counter_valid`），所以一个有效锚点配上读不到的计数器
也无法让时钟复活。

**不作任何硬件声称。** LSE 是否起振、VBAT 是否保得住计数器，以及这次等待在本块板上是否
真的完成过，全都没有测量。[`HARDWARE_TEST_PLAN.md`](HARDWARE_TEST_PLAN.zh-CN.md) 的 Stage O 正是
为这些而写的。

**冷启动的后续修正，在本分支的 GPT 审查之后加入。** 审查发现上面那次等待在*第一次*备份域
上电时仍然永远不可能成功，而且它是对的：`__HAL_RCC_RTC_CONFIG()` 就是
`MODIFY_REG(RCC->BDCR, RCC_BDCR_RTCSEL, ...)`
（`stm32f1xx_hal_rcc.h:985`），所以 `HAL_RCCEx_PeriphCLKConfig()` 只选择一个时钟源而
不使能外设。`RTCEN` 是由 `__HAL_RCC_RTC_ENABLE()`
（`stm32f1xx_hal_rcc.h:999`，一次对 `RCC_BDCR_RTCEN_BB` 的位带写）置起的，而生成的代码只在
`HAL_RTC_MspInit()` 里调用它 —— 在 `MX_RTC_Init()` 之后，也就是在这次读取本来必须发生的
时间点之后。热启动时这一位早已由上一次会话置好，因为它住在备份域里，这就是该缺陷在一次
真正的冷启动之前始终看不见的原因；而在冷启动时，它的代价是 1 s 超时加一条
`DIAG_ERR_RTC_SYNC`，而晶振其实是健康的。
`rtc_service.c` 此前在它自己的文件头注释里断言的正是相反的说法，而审查抓到的就是那条注释。

修复只涉及时序，位于 `rtc_clock_prepare()`：在 `bkp_unlock()` 之后（DBP 正是让 BDCR 那次
写入能够生效的东西），读取 `__HAL_RCC_GET_RTC_SOURCE()` —— HAL 把
`__HAL_RCC_RTC_ENABLE()` 记为"只有在 RTC 时钟源被选定之后"才可用，所以一个为零的
`RTCSEL` 返回 false 并且**不碰任何 RTC 寄存器**，连 `RSF` 的清零也不做 —— 然后
`__HAL_RCC_RTC_ENABLE()`。这次写是幂等的，所以热路径不受影响。`RTCSEL`
未被修改，备份域未被复位，`s_counter_valid` / `s_sync_failed` /
锚点语义也都没有变：`s_counter_valid = rtc_clock_prepare() && rtc_sync_before_read()`。
没有为此新增宿主测试：这个判断藏在一个 HAL 宏后面的两个寄存器位上，而把
`RCC` 打桩去断言 `x != 0` 会是一个不可能失败的测试。它是相对上面这些宏做过的 `STATIC REVIEWED`
以及 `BUILD VERIFIED`，而 Stage O (d) 现在会在硅片上测试冷启动。

## 2. RTC 备份寄存器锚点：撕裂写与断电

**寄存器预算，是核验过的而不是假设的。** 器件是 `STM32F103xB`
（`MDK-ARM/*.uvprojx:337`）→ `RTC_BKP_NUMBER 10`
（`Drivers/CMSIS/Device/ST/STM32F1xx/Include/stm32f103xb.h:860`），所以 `IS_RTC_BKP` 解析到
`(BKP) <= RTC_BKP_NUMBER` 这一支，而该宏的 `DR11…DR42` 那一半对本器件是死代码。存在十个
16-bit 寄存器；用掉了**五个**，四个用于载荷，一个用于提交标记：

| 槽位 | 寄存器 | 内容 |
| --- | --- | --- |
| `RTC_ANCHOR_W_COMMIT` | DR1 | `RTC_ANCHOR_COMMIT_VALID` (`0x2B1C`) 或 `…_BLANK` (`0x0000`) |
| `RTC_ANCHOR_W_EPOCH_LO/HI` | DR2, DR3 | epoch 秒数，两个 16-bit 半字 |
| `RTC_ANCHOR_W_COUNT_LO/HI` | DR4, DR5 | 那一刻的 RTC 计数器，两个 16-bit 半字 |

F1 的备份寄存器是 16-bit 宽，这就是每个 32-bit 字段要消耗掉两个寄存器的原因。

**这项指控，以及它是否成立。** 审查说一次被断电打断的写入可能留下新的 epoch 配旧的计数器，
它会解码成一个*看起来合理的、错误的*时间，而不是被当作损坏。**成立，而且比听起来更糟**：
`rtc_anchor_restore()` 检测不到它，因为这个混合体在算术上是自洽的 —— 新 epoch 配一个更早的
计数器会得到一个小的正向流逝值，并通过合理性检查。有一个宿主测试明确地断言了这一点
（`test_anchor_update_is_transactional`：*"一个不碰提交字就写下去的载荷就是一个混合体"* ——
镜像能解码，而 `rtc_anchor_restore()` 无论是对原样的它还是对计数器再走十秒之后的它都接受），
这样后来的人就不会靠信任这个差值去"修好"它。事后检测是不可能的；唯一正确的响应是让这个
部分状态无法解码，而这正是提交字的用途。

**修复 —— 作为一个事务发布。** `rtc_anchor_write()`（纯函数，在 `rtc_calendar.c` 里）发出
六个有序步骤：**清空提交字，写入四个载荷字，最后把提交字写成有效。** 该序列的任何一个前缀
都会让这一对无法解码，所以一次打断所能造成的最坏结果就是"没有锚点"，它被报告为*从未设置过* ——
与彻底失去备份域所得到的答案相同，而且远好于一个凭空造出来的时钟。
`anchor_write()` 重放这些步骤，然后**把这一对读回来**；如果读回来的和写下去的不一致，
它会再次清空提交字，而不是留下一个其实并不存在的值。读回的值放进一个局部变量：通过
`const rtc_anchor_t *`
参数去写会覆盖调用方的锚点（ARMCC 把那种写法报成了 error #167）。

**锚点在哪儿被写入，以及什么永远不会被存下来。** `rtc_service_poll()` 只有在软件 epoch 与一次
硬件读数在*同一个周期里被观测到*时（`s_valid && anchored`）才调用
`anchor_now()`。如果那一秒读不到硬件，就保留上一个锚点 —— 它描述的仍然是那个计数器，而计数器
一直在前进，所以下一次启动会从它重建出真实的流逝时间。当时钟从来没有被刻意设置过时，提交字是被
清空的，而不是先写一个载荷再把它作废：一个旧锚点不得让一个没人选择过的时间复活。

**双槽方案被刻意否决。** A/B 槽轮转可以让一次被打断的写回落到上一个锚点，也就是拿寄存器
数量翻倍、加上一个本身同样暴露在撕裂写之下的槽选择器、以及在槽翻转期间把同一个窗口重新引入
这些代价，去买到一秒的精度。这里的优先级被表述为*绝不把一个撕裂的锚点当作有效*，而
"先清空、再载荷、最后置提交字有效"用一个字的状态就做到了那一点。
剩下的暴露面是记录在案而不是藏起来的：**失去锚点就失去时钟**，所以一次比锚点活得更久的断电
会得到 `UNSET`，绝不会得到一个猜测。

**重建规则**（`rtc_anchor_restore`，宿主测试过）：拒绝 NULL；拒绝落在
`RTC_EPOCH_MIN_SECOND…RTC_EPOCH_MAX_SECOND` 之外的 epoch；`elapsed = (uint32_t)(counter_now -
counter_ref)`，这是回卷安全的，因为无符号模 2³² 与硬件自身的回卷一致；任何会越过
`RTC_EPOCH_MAX_SECOND` 的和都要拒绝 —— 正是这条检查抓住了那个在备份域存活期间复位了的计数器，
因为差值会变成 ~4.29e9 s。

## 3. 温度探头的故障与恢复响应

**这项指控。** 上一轮（审查修复）把确认计数器各自留在一个窗口，这是正确的，但很难解释，
而且在当时单位被错标成了毫秒。最终这一轮重新调了这些计数 —— 规范明确授权这么做 ——
并且如实命名了单位。**ADC 门限没有被改动**：`TEMP_ADC_OPEN_THRESHOLD` 和
`TEMP_ADC_SHORT_THRESHOLD` 仍然带着它们的 `/* UNVERIFIED */` 标记，因为模拟前端的一切都
还不存在。

**名字用窗口来记，不用假毫秒**（`App/config/temperature_calibration.h`）：

```c
#define TEMP_PROBE_FAULT_CONFIRM_WINDOWS  2U
#define TEMP_PROBE_OK_CONFIRM_WINDOWS     4U
#define TEMP_UPDATE_PERIOD_MS  ((uint32_t)TEMP_AVERAGE_WINDOW * 1000U / (uint32_t)ADC_SAMPLE_RATE_HZ)
```

`TEMP_UPDATE_PERIOD_MS` 是推导出来的，不是手填的：250 个采样在 1 kHz 下 = 250 ms。

| 响应 | 计数 | 时间 |
| --- | --- | --- |
| 探头开路或短路 → 锁存 `PROBE FAULT` | 连续 2 个故障窗口 | ≈ **500 ms** |
| 探头恢复 → 故障清除 | 连续 4 个健康窗口 | ≈ **1000 ms** |
| 单个孤立的离群窗口 | 1 | 两个方向都不改变任何东西 |

刻意做成不对称的：一个报警不得因为一次坏的平均值就触发，而清除一个报警应当比拉起一个报警
需要更多证据。两条编译期断言保留了下来 —— 一条说更新周期是整数毫秒，一条说它满足 ≤ 500 ms
的刷新要求；第三条断言在窗口成为定义本身之后就变成了同义反复，于是被删掉，而不是留着当装饰。

`tests/host/test_temperature.c:143` 的 `test_probe_confirm_is_window_by_window` 一个窗口
一个窗口地走完喂入序列，并断言：一个故障窗口不会锁存，第二个会；
恢复需要四个全都到位；交替输入永远到不了任一个计数；每一段连击在遇到相反输入时都会复位。
之前那个测试只断言了*最终会*，这就是它对着错误的单位也能通过的原因。

## 4. 验证

下面每一个数字都是由所示命令、在本分支上、在最后一次编辑之后产生的。

| 类别 | 含义 | 适用于 |
| --- | --- | --- |
| `HOST VERIFIED` | 在宿主编译器上编译并执行过（经 `ziglang` wheel 的 clang） | `rtc_calendar.c` 里的纯锚点模型、温度状态机、协议、日历 |
| `BUILD VERIFIED` | 为目标编译干净，从未执行 | `rtc_service.c`、`app.c`、`diagnostics.c`、`temperature.c` 面向 HAL 的胶合层 |
| `STATIC REVIEWED` | 对着仓库内置的 HAL/寄存器映射表读过，什么都没运行 | 寄存器数量、`RSF` 语义、调用顺序 |
| `HARDWARE VERIFIED` | 在硅片上测量过 | **什么都没有** |

```
# 1. firmware, from-scratch rebuild
MDK-ARM> /c/Keil_v5/UV4/UV4.exe -j0 -r "Human Heart and Body Temperature Monitor.uvprojx" -o final_rebuild.log
   → 0 Error(s), 0 Warning(s)   Code=38392 RO-data=3096 RW-data=380 ZI-data=7524

# 2. host C algorithms (1050 assertions)
pc_monitor/.venv/Scripts/python.exe tools/run_host_tests.py
   ecg pipeline 97 · oled font format 78 · protocol+crc16 624 · rtc calendar 189 ·
   temperature (uncalibrated) 50 · temperature (linear model) 12

# 3. PC side (256 cases, GUI included)
cd pc_monitor && QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m pytest -q
   → 256 passed in 44.61s

# 4. golden vectors must not move
pc_monitor/.venv/Scripts/python.exe tools/gen_protocol_vectors.py
   → wrote tests/host/protocol_vectors.json (21 vectors, ECG_BATCH frame 69 bytes)
   → git diff on that file: EMPTY, so PROTOCOL_VERSION stays 2
```

`.ioc` 不在 diff 里；没有任何 vendored HAL 或 CMSIS 文件在 diff 里；没有任何生成的 `Core/`
文件在 diff 里。触及工程生成侧的唯一改动，仍然是本来就存在的 `USER CODE BEGIN RTC_Init 0` / `RTC_Init 2` 调用点。

## 5. 审查提示里哪些是错的、不完整的，或者是对的

* **本分支的 GPT 审查在冷启动这件事上是对的**，而这是本轮刚写下的代码里的一个真实
  缺陷：RSF 等待的边界是正确的，却被放在任何东西给 RTC 接口供时钟之前。§1 收录了后续修正。
  它要求在做使能之前 *核实 RTC 时钟源已被选定*，那才是值得保留的部分：没有时钟源的情况不是一个
  等得出结果的超时，所以它现在提前返回，一个 RTC 寄存器都不碰。
* *"核实这个器件到底暴露了多少个备份寄存器"* 是值得做的：
  `IS_RTC_BKP` 宏*看起来*像允许 DR42，而在本片上并不允许。
  十个里用五个，远在限制之内，而 DR6–DR10 仍然空着。
* 一个撕裂的锚点**确实**会被静默接受，只要提交字恰好以有效状态幸存 ——
  事后检测是不可能的，而这正是清空那一次写要排在最前面的全部理由。这条回归测试的第一稿断言了
  相反的结论，必须把它倒过来，才能记录真正的危险。
* *"把计数器 0 当作可疑"* 会是错的：0 在一个 epoch 刚开始时是一个完全正当的计数器值。实现出来的
  规则正好相反 —— 让计数器不可用的是一次**未同步的读取**，无论它读出的是什么。
* 在不改变抽取的前提下把探头确认加速到一个平均窗口以下，在本设计里做不到，而改变抽取会破坏
  ≤ 500 ms 刷新的算术。500 ms/1000 ms 这一对是仍然保得住不对称性的最快组合。

## 6. 本轮更新的文档

`docs/HARDWARE_TEST_PLAN.md`（Stage K 的时间已改正 —— 它们从一个早已不存在的常量名得出了
~125 ms/~250 ms；新增的 **Stage O** 覆盖 RTC 跨复位与断电的连续性，并把三层故障分开）、
`docs/COURSE_REQUIREMENTS.md`（探头行、三层 RTC 行、新增的拒绝猜测行、日历计数、占用）、
`docs/ARCHITECTURE.md`（模块图的措辞）、`README.md` 和 `README.zh-CN.md`（占用、
测试计数、Stage 范围、分支/链路、本文档），以及往
`docs/PHASE1_REVIEW_HANDOFF.md` 和 `docs/PHASE1_REVIEW_FIX_HANDOFF.md` 里加的前向指引说明 —— 两份都保留为它们本来的样子，
也就是历史记录，而不是被重写。

## 7. 软件冻结

阶段 1 的应用软件在此冻结，位于 `phase1/final-fixes` 的末端。

本轮结束仍然未知、并且不可声称的：

1. **任何形式的硬件行为都没有。** 没有 LSE 起振，没有 VBAT 保持，没有面板像素，
   没有 ADC 输入量程，没有人体测量，没有真实的 1 kHz 带 UI 的持续时序。
   本轮让 RTC 的失效模式变得诚实；它没有让其中任何一项变成被观测到的。
2. **`rtc_service.c` 从未执行过。** 它的顺序是针对
   `HAL_RTC_Init()`、`HAL_RTC_WaitForSynchro()`、
   `RTC_ReadTimeCounter()`、
   `__HAL_RCC_RTC_CONFIG()`、`__HAL_RCC_RTC_ENABLE()` 和
   `HAL_RCCEx_PeriphCLKConfig()` 做过 `STATIC REVIEWED` 的 —— 包括这两条发现：一次正常的 LSE 启动
   *不会*复位备份域，因为 `HAL_RCCEx_PeriphCLKConfig` 只在时钟源真的改变时才触发 `BDRST`；以及
   `RTCEN` 在 `HAL_RTC_MspInit()` 之前的任何地方都没有被置起。由 Stage A/B/O 裁决；Stage O (d)
   就是本轮修掉的这个冷启动。
3. **温度按设计就是未标定的**，而开路/短路门限是带着 `UNVERIFIED` 标记的猜测。§3 里的探头响应
   时间对 Stage J 最终定下来的任何门限都是正确的。
4. **`last_error_code` 不在任何一个屏幕上。** 只有 `protocol_errors` 跨过链路出去，所以 RTC
   同步失败表现为 UART 上的一个标志加一个自增计数器，以及调试器下的一个符号。
5. **上一轮 KK_UI 的 KEY_OK 页面焦点改动仍然未执行过** —— 硬件计划的 Stage C 会测试两个方向。

这一轮被要求止步于一次 push：不合并、不移动 `v0.1-baseline`、不开阶段 2。
合并是审查者该做的决定，而在上面那个冷启动缺陷被确认之后，这个决定做了 —— `phase1/final-fixes`
在 `8d5c3c9` 作为一次合并提交被并入 `main`，三条审查分支原地保留作为记录。`v0.1-baseline` 仍然指向
阶段 0，而阶段 1 不存在任何 release 标签：`main` 上的是一个从未与它所写的硬件见过面的软件。

下一份工作是对着
[`HARDWARE_TEST_PLAN.md`](HARDWARE_TEST_PLAN.zh-CN.md) 做硬件上电调试，从 Stage A 开始。
