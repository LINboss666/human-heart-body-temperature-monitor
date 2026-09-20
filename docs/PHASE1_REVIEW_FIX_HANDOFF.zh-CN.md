# 阶段 1 审查修复交接包

[English](PHASE1_REVIEW_FIX_HANDOFF.md) · **中文**

| | |
| --- | --- |
| 分支 | `phase1/review-fixes`（已推送；**并未**合入 `main`） |
| 起始提交 | `b10c07fc09629596e387c96c5f1424ad81392e20` |
| 本次要审的 | 分支头部。六处代码修复是 `5f02b33..a805625`；其后的 `7993e1f` 和 `0b0adfc` 只改文档和一条过严的 GUI 断言 |
| 冻结基线 | 标签 `v0.1-baseline` → `eb8a795`（未动过） |
| 变更集 | 37 个文件，+1337 / −276，其中六处代码+测试修复占 30 个文件 |
| 固件构建 | `0 Error(s), 0 Warning(s)`（完整 `-r` 重建） |
| 资源占用 | `Code=38152 RO=3076 RW=380 ZI=7524` → flash 41608/65536 = **63.5 %**，RAM 7904/20480 = **38.6 %** |
| 宿主 C 测试 | 6 个可执行文件，**1004 条断言，0 失败**（此前是 901） |
| Python 测试 | **256 个用例，0 失败**（此前是 251），含无头 GUI |
| 黄金向量 | 重新生成后**无差异** → 线上格式未变 → `PROTOCOL_VERSION` 保持为 **2** |
| 硬件已验证 | **无。** 本轮未改变这一点 |

> **有三点已被 [`PHASE1_FINAL_HANDOFF.md`](PHASE1_FINAL_HANDOFF.zh-CN.md) 取代**，
> 那才是这条审查链当前的头部：上面表格里的资源占用与宿主测试总数是 `phase1/review-fixes`
> 的数字；§1 的温度探头计时（"31.25 s / 62.5 s"、每个计数一个窗口）描述的是本轮原样保留的
> 那套确认方案，而最后一轮刻意把它重新调成了 2 个和 4 个窗口。此处其余内容仍然与代码现状
> 一致。

本轮开始时的阶段 1 状态请读 [`PHASE1_REVIEW_HANDOFF.md`](PHASE1_REVIEW_HANDOFF.zh-CN.md)。
本文档只记录审查发现了什么、每一条主张是否成立，以及改了什么。

---

## 1. 结论

每一条主张都先从源码复现过。**七条里有六条成立；有一条在它自己的代码层面成立、在它预测
的后果层面却不成立，而它身后还藏着一个更严重的缺陷。**

| # | 主张 | 结论 |
| --- | --- | --- |
| P0-1 | RTC 丢失 VBAT 供电期间走过的时间 | **CONFIRMED** |
| P0-2 | ECG_BATCH 的温度一直是过期/零值 | **CONFIRMED，而且恒为零** |
| P0-3 | 过期的 ECG 页面状态使 KEY_OK 在其他页面上触发 | **CONFIRMED（过期状态）/ NOT REACHABLE（那个后果）** —— 该动作在任何地方都从未触发过 |
| P1-1 | 探头确认计数单位是更新次数，不是毫秒 | **CONFIRMED** |
| P1-2 | DSP 中存在有符号移位的 UB | **CONFIRMED**，只在一处；审计发现其余各处本就是定义良好的行为 |
| P1-3 | OLED 的 "PC stream" 开关是死的 | **CONFIRMED** |
| P1-4 | 20 抽头方框积分器被写在两个不同的转折频率上 | **CONFIRMED**；原因是生成器 |

### P0-1 — RTC 断电期间的连续性

`preserve()` 只保存了镜像的 epoch。CubeMX 的 `HAL_RTC_SetTime()` 随后又用当日秒数覆盖了
硬件计数器，而 `restore()` 把那个已过期的 epoch 重新装了回去。RTC 在 VBAT 上走过的每一秒
都被丢弃了。

该主张低估了 F1 的处境。这里根本没有任何持久化的日历：`HAL_RTC_SetDate()` 只写入
`hrtc->DateToUpdate`（RAM），而 `HAL_RTC_GetTime()` 会把计数器里整数的天折算回那块 RAM。
因此那个裸的 32 位计数器是跨一次电源循环的*唯一*证据 —— 而它被读取得太晚，或者压根没读。

**修复。** 备份寄存器现在保存的是一个*锚点*：DR1 放魔数、DR2/DR3 放 epoch、DR4/DR5 放
计数器，每个 32 位数值拆到两个 16 位的 F1 寄存器里。`preserve()` 在 `hrtc.Instance` 尚不
存在之前，就直接通过 `RTC->CNTH/CNTL` 采样裸计数器（并使用 HAL 同一套高/低/高重读），
`restore()` 则计算 `epoch + (counter_now - counter_anchor)`。无符号减法自动给出计数器自身
在 2³² 处的回绕；一个会落到 2099 年之后的增量会被**拒绝** —— 这就是如何捕获"计数器在仍然
存活的备份域之下被复位了"这种情形，而不是把它报告成下个世纪的日期。锚点只在那些已知成对
自洽的时刻重写（一次被采纳的硬件读取之后，或一次 `hw_set()` 之后）；如果时钟读不出来，就
沿用上一个锚点，因为它描述的仍然是一个在持续前进的计数器。

满足的需求：上电不再抹掉 VBAT 上已经走过的时间；NRST 会保留它；备份域丢失可经由魔数检出；
首次启动仍可区分；不伪造时间戳；1970–2099 予以保留。

**已在宿主上测试**（`rtc_anchor_restore`，`rtc_calendar.c` 里的纯算术）：同一次启动内的
推进、软件复位、断电 1 小时与 36 小时、计数器回绕、跨越闰日、跨越新年、不合理增量的拒绝、
NULL 的拒绝，以及把记录在案的 epoch 上限拿去和 `rtc_to_epoch()` 对照校验，而不是当成字面量
采信。**`rtc_service.c` 本身只是 BUILD VERIFIED** —— 它需要 HAL。VBAT 保持仍是一台从未被
测量过的硬件；这个修复使那段时间间隔在器件确实保得住的前提下*可以*被恢复，并且并未声称它
确实保得住。

### P0-2 — ECG_BATCH 的温度

比"过期"更糟。`send_ecg_batch()` 读的是 `s_temp_raw_latest` / `s_temp_centi_latest`，
而它们唯一的写入者就在 `protocol_service_push_sample()` 内部的 `if (temp != NULL)` 之下 ——
并且唯一的调用方传的是 `NULL`。这两个字段在**所构造过的每一帧里都是零**，而 `TEMP_STATUS`
载着真实情况，于是实时视图看上去是对的，记录却是错的。

目前它被 `TEMP_MODEL_UNCALIBRATED` 把度数那一列清空所掩盖。探头一旦被校准，每一条导出的
行都会读成**人体的 0.00 °C** —— 一个凭空而来、却看似合理的数字。它之所以静默，是因为那个
尾部参数是一组*可选*实参：`NULL` 是一个合法的调用，含义就是"让它保持过期"。

**修复。** 尾部现在从诊断快照取数 —— 那早已是 STATUS 报文与 OLED 的唯一来源 —— 并新增了
`temp_raw`，使原始码值在尚未校准时依然可用。那个可空参数被**移除**了，因此这一失效模式无法
被重新引入。每个采集块（128 ms）取一次，比 20 ms 的批次更粗，而对一个按 250 ms 抽取的信道
来说无关紧要。

顺带在此发现的：PC 侧的 `temp_valid_rows` 数的是*批次*，而 XLSX Summary 的标签写的是
"Temperature samples valid" —— 少报了正好一个批大小。已改名为 `temp_valid_reports` /
"Temperature reports valid"。

**已测试：** `pkt_write_batch_tail()`/`pkt_read_batch_tail()` 现在只在一个地方施加
`ECGT_*`；宿主测试覆盖往返、负的 centi 值，以及绝不写到 `ECGP_TAIL` 之外。Python 测试证明
每个批次把自己那份温度带进自己那些行、器件侧卡住的零仍然是零且绝不被换算成度数、已校准与
未校准的批次保持可区分，以及 C 生成的黄金向量能一路存活进记录器。

### P0-3 — ECG 页面状态与 KEY_OK

镜像的 `s_current_page` 确实从未被清除，`ui_app_on_ecg_page()` 确实也在导航之后仍然保持为
真。但审查预测的那个后果 —— 在菜单项上按下确认会切换记录 —— **不可能发生**：
`handle_buttons()` 要求 `ev.kind == BTN_EVT_SHORT`，而 `buttons.c` 只推入过 `PRESS`、
`RELEASE` 和 `LONG`。`SHORT` 被声明了、在头文件里被写成应用层动作的来源，却没有任何代码
路径发出它。该条件从未为真，所以 **KEY_OK 从来没有启动或停止过任何东西，在任何页面上都
没有。** 好处与危险同时都不存在。

**修复。** 动作移到了 `KK_UI_CustomOnInput`，而 `KK_UI_DispatchInput()` 只在一个
`KK_UI_PAGE_CUSTOM` 页面持有焦点时才会调用它 —— 也就是真正知道焦点的那个组件，而不是它的
一份副本。`KK_UI_CustomOnLeave` 现在会清除镜像，于是 `ui_app_on_ecg_page()` 只作为一种波形
绘制优化而留存，而它剩下的滞后（KK_UI 会把 `OnLeave` 推迟到滑动动画结束）已在 `ui_app.h`
里记录为无害。

`buttons.c` 那个半成品事件队列随着它唯一的消费者一起被删掉了。去抖与原始按键掩码 —— 
KK_UI 的移植契约所要求的全部内容 —— 保留下来。

**验证状态：BUILD VERIFIED + STATIC REVIEWED，未做宿主测试。** `ui_app.c` 需要 KK_UI、
KK_OLED 和 HAL。这个修复的证据是 `kk_ui.c:506-518`（dispatch 依据实时页面类型分支；
`CUSTOM` → `CustomInput`）和 `kk_ui_nav.c:44-50,133-139`（`leave_custom` 只在离开的路线是
`CUSTOM` 时才被置位），两处都按 file:line 给出引用。因此 §4 里那张验收矩阵**不是**机器校验
过的，并被列成了硬件计划中的一项。

### P1-1 — 探头确认的单位

这些连击计数只在 `temperature_feed()` 的窗口保护之后才递增，所以
`TEMP_PROBE_FAULT_CONFIRM 125` 是 125 × 250 ms = **31.25 s**，不是它的注释所声称的
"125 ms at 1 kHz"，也不是"连续采样"。

**计数刻意保持不变。** 在一处注释修复的掩护下，去重新调一个从未见过它所描述的硬件的门限，
等于把一个记录在案的错误换成一个没有记录的改动 —— 而且开路/短路的门限本身仍然还是
**UNVERIFIED**。

`TEMP_UPDATE_PERIOD_MS` 是 `app_config.h` 里紧挨着 `TEMP_AVERAGE_WINDOW 250U` 的第二个手写
`250U`；这处重复已经消失，周期改为推导得出，并带三条编译期断言（整数毫秒、≤ 500 ms 的粗略
要求、确认乘积有意义）。测试锁定故障恰好在第 CONFIRM 次更新时置起，断言再早一次更新不会置起，
并把代价陈述为 31.25 s。

### P1-2 — 有符号移位

`d = ((bandlimited << 1) + s_deriv_x1 - s_deriv_x2) >> ECG_DERIV_SHIFT;` 对一个有符号值做了
左移 —— 在该模块声称遵循的 C 里这是未定义行为，而本文件自己的文件头就写明要避免有符号移位，
因为两个编译器必须给出一致结果。其余每一级都用 `idiv_pow2()`；只有这一行是例外。

现在是 `bandlimited * 2`，然后 `idiv_pow2`。这会在一个平方级之前把负中间值挪动 ≤1 个计数
（向下取整的移位 → 向零截断的除法）。**实测响应未变**：检测器复现出 50→49、60→60、72→72、
95→95、120→120、150→150，97 项检查全部通过，并且平线仍然不产生幻影 QRS。

审计了所有移位点，而不只是被报告的那一处：`remove_baseline` 的输入是一个经 `uint16_t` 拓宽
的 12 位码值 —— 可证明非负且余量充足（不过仍然改成了乘法）；平方的 `>>` 运行在 `|d|` 之后；
`box_run()` 结束于 `clamp_i16()`，它把导数的四项和约束在远小于 int32 的范围之内，从而使
`-d` 取反是安全的；`ema_step()` 只移位幅值，其上限由 `0xFFFF` 的能量钳位界定。

### P1-3 — PC 数据流开关

`case EVT_STREAM_TOGGLE:` 是一条空语句，藏在一句声称 `App_Loop` 会轮询 `v_stream` 的注释
后面。它并没有轮询，而 `ui_app_stream_switch()` 也没有任何调用者。这个开关是一个死控件。

`protocol_service` 现在是唯一的流控状态。这个开关会下发一条真正的命令 —— 并且要注意 KK_UI
在事件被轮询之前就已经通过绑定提交了新值（`kk_ui_dialog.c:335-346`），所以处理函数读到的是
*请求*，而不是旧状态。UI 每次更新都重新读取协议侧的值，因此 PC 发来的 `START_STREAM` 是被
显示出来，而不是被对抗。

记录在案的冲突规则：**最后一条显式命令获胜**，就一个布尔量。ECG 页面上的 KEY_OK 仍然把
记录→流送耦合在一起；开关和 PC 只移动流送本身，这正是那个合法的"不保存只看实时"模式。

### P1-4 — 滤波器响应的文档

同样是 20 个抽头、同样的 fs，却出现 `ECG_QRS_LP_M3DB_HZ 43.8` 与 `ECG_COMB_50HZ_M3DB_HZ 22.1` ——
二者必有一个是错的。错在生成器：它测量的是 `h_qrs`（5 Hz 高通与低通级联）相对于**它在 1 Hz
处自身的增益**，而且是在高通阻带内 —— 那里级联已经下降 14 dB。算术上正确、语义上毫无意义，
随后又被错标成了低通的转折频率。

每个数字现在都写明了自己的参考。方框积分器单独对单位增益：**22.2 Hz**（两处一致）。级联则
按它本来的面目描述为带通：峰值在 11.2 Hz、为 −1.7 dB，相对峰值的 −3 dB 点是 **3.8–26.3 Hz**。
因此那句声称 QRS 通带为 **5–44 Hz** 的文字同样是错的 —— 级联在 44 Hz 处已下降 17.7 dB —— 
已在 `README.md`、`README.zh-CN.md` 和 `ecg_config.h` 中更正。

运行时 DSP 未动。新增的宿主测试驱动出厂的那套滤波器，并把生成的头文件与它对照检查：结构与
fs 被钉住，两个 20 抽头节段被强制共用同一个转折频率，并要求该通带保持在 40 Hz 以下，好让
旧的说法无法回来，实测增益在 10/22/26/30 Hz 处与解析的 MA(20) 相差不超过 0.9 dB，以及 FS/20
处的零点。**方法说明：** `ECG_NOTCH_OFF` 选出的是一个单抽头直通，所以任何响应测量都必须用
50 Hz 梳状滤波器 —— 该测试早先的一稿测量的是平坦度，并且因为错误的理由通过了。

---

## 2. 审查未列出、但本轮发现的缺陷

1. **`BTN_EVT_SHORT` 从未被发出**（见上文 P0-3）。一个被声明、被记录、被拿来比较的事件，
   却没有任何生产者。严重度高于被报告的那个症状：主要的启动/停止控制完全是死的。
2. **`temp_valid_rows` 数的是批次，却被导出成 "Temperature samples valid"** —— XLSX Summary
   里一个并不等于其标签含义的数字。
3. **`TEMP_UPDATE_PERIOD_MS` 重复了 `TEMP_AVERAGE_WINDOW`** —— 另一个头文件里一个未受检查的
   第二个字面量，正是审查要求留意的那一类重复尺寸常量。
4. **一个不可能失败的响应测试。** 值得记录下来，因为它是有趣的那一类：我的第一版在 notch 为
   `OFF` 的情况下测量显示通路，而 `OFF` 是单抽头直通，于是滤波器看上去是平的，每一条
   "它会有滚降"的断言都会因正确的理由而失败，而一条"它是平的"的断言则会因错误的理由而通过。

## 3. 刻意没有做的事

- 没有 bump `PROTOCOL_VERSION`。21 个黄金向量重新生成的结果字节完全相同，包括 `ecg_batch_*`
  那几条，所以线上格式没有移动。
- 没有做 CubeMX 重新生成。只有 `Core/Src/rtc.c` 中已有的 `USER CODE BEGIN
  RTC_Init 0` 注释被改动；本轮没有触碰用户区域之外的任何生成行。
- 没有重新调探头故障门限（见 P1-1）。
- 没有改动任何 vendored 文件。KK_UI 与 KK_OLED 与 `UPSTREAM.md` 所记录的一字不差、逐字节
  一致；`KK_UI_CurrentPageType()` *没有*被应用代码调用，因为它只在 `src/kk_ui_internal.h`
  里声明，而伸手去拿一个私有头文件是比它能省下的一次性调用更糟的依赖。
- 没有改动 `main.c`、引脚映射、`.ioc` 或 Keil 目标设置。
- `tools/_restamp_golden.py` 予以保留：它是手写黄金 hex 的来历凭据。

## 4. P0-3 的验收矩阵，以及它的真实状态

所要求覆盖：初始页面 ≠ ECG；导航到 ECG → 谓词为真；ECG→MAIN、ECG→TEMP、ECG→SETTINGS →
为假；在 RTC/settings 页面上按 KEY_OK 不得切换；在 ECG 页面上按 KEY_OK 必须仍然有动作。

这七条全部是 **STATIC REVIEWED（对照 KK_UI 的 dispatch 代码）+ BUILD VERIFIED，但没有做宿主
测试**。`ui_app.c` 若没有 KK_UI + KK_OLED + HAL 桩就无法在宿主上编译，而搭那套脚手架被判定为
与剩余风险不成比例。[`HARDWARE_TEST_PLAN.md`](HARDWARE_TEST_PLAN.zh-CN.md) 的 **阶段 C**（*三个按键*）
和 **阶段 E**（*像素，以及选择控制器配置档*）在真实面板、真实按键上覆盖了它；在那之前，这是
本文档里唯一一个行为尚未真正执行过的修复。请加入阶段 C：
"start a recording with KEY_OK on the ECG page, then navigate to SETTINGS and press KEY_OK
again — the recording state must not change."

## 5. 仍然存在的已知弱点

原样结转，因为本轮没有处理其中任何一项：停止时不完整的 `ECG_BATCH` 从不被刷出；阻塞式 UART
与 I2C 共用主循环；仅 ASCII 的单字体 UI；演示里那 3 s 的 `ACQUIRING` 镜像；作为启发式的
`looks_like_demo()`；XLSX 图表从未在真正的 Excel 里打开过；RTC 修复之后没有再运行过 CubeMX
重新生成，所以那是一条预测，而不是观察。

本轮新增：

1. **RTC 锚点的一致性是 ±1 s，并不精确。** 锚点的 epoch 半边与计数器半边是背靠背读回的，而非
   原子读取，因此重建结果每一次电源循环最多可能差一秒。有界且不累积。未在硬件上测量。
2. **`ui_app_on_ecg_page()` 仍然滞后一次页面切换**（≤ KK_UI 的 `KK_UI_PAGE_MS`）。现在只有
   一个绘图优化依赖它。
3. **`rtc_service.c` 的调用顺序是靠审查断言的，而不是靠测试。** `preserve()` 在前、
   `restore()` 在后，以及 `init()` 内部的那次重新锚定，全都取决于 CubeMX 把用户区域放在了
   哪里。

## 6. 复现

```
# firmware: full rebuild, log must end "0 Error(s), 0 Warning(s)"
"C:\Keil_v5\UV4\UV4.exe" -j0 -r MDK-ARM/"Human Heart and Body Temperature Monitor".uvprojx -o build.log

# 1004 assertions across 6 host binaries
pc_monitor/.venv/Scripts/python.exe tools/run_host_tests.py

# 256 cases, headless GUI included
QT_QPA_PLATFORM=offscreen pc_monitor/.venv/Scripts/python.exe -m pytest pc_monitor/tests -q

# must rewrite tests/host/protocol_vectors.json with NO git diff
pc_monitor/.venv/Scripts/python.exe tools/gen_protocol_vectors.py

# regenerates App/config/ecg_filter_coeff.h; prints every measured figure
python tools/gen_ecg_filters.py
```

这四条都为本文档实际运行过；顶部表格里的结果来自那些运行，而不是来自推断。

## 7. 测试诚实度

| 类别 | 本轮 |
| --- | --- |
| `HOST VERIFIED` | RTC 锚点算术、批次尾部访问器、以两种单位表述的探头确认计时、出厂滤波器的 MA(20) 响应、温度按批次在 PC 记录器中的传播、成帧、CRC、日历、字模解码、CSV/XLSX 导出 |
| `BUILD VERIFIED` | `rtc_service.c`、`protocol_service.c`、`app.c`、`ui_app.c`、`buttons.c`、`ecg_signal.c` —— 编译干净，0 警告 |
| `STATIC REVIEWED` | 对照 KK_UI 的 dispatch 审查 P0-3 的页面焦点行为；CubeMX 重新生成的安全性 |
| `HARDWARE VERIFIED` | **无。** 没有连接任何开发板。关于 VBAT 保持、LSE 起振、OLED 控制器或地址、真实的 1 kHz 计时、前端增益或人体测量准确度，本轮没有任何一点发生变化，其中任何一点都不可以被声称。 |

---

PHASE 1 REVIEW FIX READY FOR GPT REVIEW
