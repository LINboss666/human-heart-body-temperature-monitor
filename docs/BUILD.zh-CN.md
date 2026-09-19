# 构建 — 阶段 0 基线

[English](BUILD.md) · **中文**

目标 IDE：**Keil MDK-ARM (uVision)**。本仓库中没有 Makefile、CMake 或 `arm-none-eabi-gcc`
的构建，并且 CubeMX 被指定为只生成一个 MDK-ARM 工程
（`ProjectManager.TargetToolchain = MDK-ARM V5.32`）。

## 基线结果

```
0 Error(s), 0 Warning(s).
Program Size: Code=6804  RO-data=328  RW-data=16  ZI-data=2000
Build Time Elapsed: 00:00:04
```

这是一个**从零重新验证**过的结果，而不只是对用户早前那次构建的抄录。为什么这个区分很重要，
见下文的"验证历史"一节。

## 如何复现

1. 安装 MDK-ARM，并带器件包 `Keil.STM32F1xx_DFP@2.2.0` 与 `ARM::CMSIS@6.2.0`。
2. 打开 `MDK-ARM/Human Heart and Body Temperature Monitor.uvprojx`。
3. 选择 target **Human Heart and Body Temperature Monitor**（唯一的 target）。
4. **Project → Build Target**（`F7`），或 **Rebuild all target files**（`Ctrl+F7`）。

命令行（Windows，在 `MDK-ARM` 目录下）：

```
"C:\Keil_v5\UV4\UV4.exe" -j0 -b "Human Heart and Body Temperature Monitor.uvprojx" -o build.log
```

退出码 `0` 表示既无错误也无告警。

## Keil target 设置（读取自 `.uvprojx`，未作改动）

| 设置 | 值 |
| --- | --- |
| Target 名称 | `Human Heart and Body Temperature Monitor` |
| 器件 | `STM32F103C8` (STMicroelectronics)，器件包 `Keil.STM32F1xx_DFP.2.2.0` |
| 内核 | Cortex-M3 (`"Cortex-M3"`) |
| ROM (IROM) | `0x08000000`，大小 `0x10000` → 64 KB |
| RAM (IRAM) | `0x20000000`，大小 `0x5000` → 20 KB |
| 预处理器宏定义 | `USE_HAL_DRIVER, STM32F103xB` |
| 头文件搜索路径 | `../Core/Inc`、`../Drivers/STM32F1xx_HAL_Driver/Inc`、`../Drivers/STM32F1xx_HAL_Driver/Inc/Legacy`、`../Drivers/CMSIS/Device/ST/STM32F1xx/Include`、`../Drivers/CMSIS/Include` |
| 优化级别 | Level 3 (`-O3`)，时间优化关闭（`oTime=0`） |
| 告警级别 | 2 —— Keil 的默认值，"all common warnings"。不是 3 级，所以原则上可能存在某条被级别抑制的诊断；全量重建没有产生任何告警文本。 |
| C 标准 | C99（`uC99=1`），GNU 扩展关闭（`uGnu=0`），严格 ANS C 关闭（`Strict=0`） |
| Interwork | 已启用（`interw=1`）—— ARM/Thumb 互操作段 |
| MicroLIB | target 的 `Cads` 块中不存在 `useUlib` 元素，即标准 C 库 |
| 分散加载文件 | 未提供 —— `umfTarg=1`，即 ROM/RAM 取自 Target 对话框，`.sct` 自动生成 |
| Hex 文件 | `CreateHexFile=1`，输出在 `.axf` 旁边 |
| 输出目录 | `Human Heart and Body Temperature Monitor\`（含空格的路径；见坑点） |
| Linker/LTO | AC5 (ARMCC V5.06)，无 LTO |

## 验证构建实际使用的工具链

```
MDK-ARM Plus        Version 5.43.0.0
Toolchain path      C:\Keil_v5\ARM\ARMCC\Bin
Armcc/Armasm/ArmLink V5.06 update 5 (build 528)
CPU DLL             SARMCM3.DLL V5.43.0.0
Target DLL          STLink\ST-LINKIII-KEIL_SWO.dll V3.3.1.0
CMSIS               ARM::CMSIS 6.2.0 (CORE component 6.1.1)
Device DFP          Keil.STM32F1xx_DFP 2.2.0
```

注意 `.ioc` 是用 **CubeMX 6.17.0** 编写的，它发出的 MDK 工程面向 **MDK-ARM V5.32**；本地 IDE 是
**5.43**。生成的工程在 5.43 上用 AC5 可以干净地打开并构建，因此这个版本漂移只是表面问题。之所以
记录它，是因为一个使用不同 MDK 版本的审阅者应当知道哪一种组合是被证明过的。

## target 编译的源码清单

共 31 个文件：

* `startup_stm32f103xb.s`
* 来自 `Core/Src/` 的 11 个文件（`main`, `gpio`, `adc`, `dma`, `i2c`, `rtc`, `tim`, `usart`, `stm32f1xx_it`, `stm32f1xx_hal_msp`, `system_stm32f1xx`）
* 来自 `Drivers/STM32F1xx_HAL_Driver/Src/` 的 19 个文件
* 1 个通过 RTE 解析的 CMSIS 组件（`ARM::CMSIS:CORE`）

分组：`Application/MDK-ARM`, `Application/User/Core`, `Drivers/STM32F1xx_HAL_Driver`,
`Drivers/CMSIS`, `::CMSIS`。

> **仓库体积观察（未采取行动）。** `Drivers/` 在约 98 MB 的目录树里占约 91 MB，而其中大部分 ——
> `Drivers/CMSIS/Lib`（35 MB 预编译的 `arm_*_math.lib`）、`Drivers/CMSIS/DSP`（13 MB）、
> `Drivers/CMSIS/NN`、`Core_A`、`RTOS`、`RTOS2`、`examples` —— 是 CubeMX 拷进来的，
> 并**不**在上面的编译清单里。阶段 0 按要求保持 `Drivers/` 完整不动；缩减它是日后某一次单独、
> 显式提交的候选项，而不是一处"顺手为之"的改动。

## 验证历史 —— 在相信任何"0 errors"说法之前先读这一节

本工作区里的文件时间戳讲出的故事比"它构建好了"更精确：

| 时间 (2026-09-18) | 事件 | 证据 |
| --- | --- | --- |
| 21:15 | CubeMX 创建了工程骨架 | 目录 mtime |
| **21:17** | **用户的 Keil 构建 → `0 Error(s), 0 Warning(s)`, `Code=6748`** | 原始的 `*.build_log.htm` |
| **21:18:39 – 21:18:58** | **CubeMX 重新生成了工程** —— `.ioc`、`Core/Src`+`Core/Inc` 全部内容、`.mxproject`、`.uvprojx`、`.uvoptx` | 文件 mtime |
| 21:34 | 从**当前**源码做一次干净的全量重建（`UV4 -j0 -r`）→ `0 Error(s), 0 Warning(s)`, `Code=6804` | 本次会话 |

**推论：** 用户最初看到的那次构建针对的是最后一次 CubeMX 写入*之前*的那一代产物，所以它的
`Code=6748` 并不描述已提交的这棵树。已提交的基线由 **21:34** 那次全量重建来描述：
`0 Error(s), 0 Warning(s)`, `Code=6804`。这 56 字节的差值与"重新生成的源码与上一次生成略有不同"
相吻合；两个方向上都没有任何源码被手工编辑过。

这两个事实都被如实报告而不是被抹平，因为"基线干净构建"必须意味着*就是这棵特定的树*干净构建。
它确实如此，而且这一点现在已经从零证明过了。

那次全量重建只写入了 `MDK-ARM/Human Heart and Body Temperature Monitor/`，该目录被 git 忽略。
事后核实：`Core/` 与 `Drivers/` 下的文件，以及 `.ioc`、`.mxproject`、`.uvprojx`、`.uvoptx`，
在 21:19 之后都没有变化。

## 坑点

* **工程名包含空格**（目录、`.ioc`、`.uvprojx`、输出目录）。命令行构建必须给路径加引号。
  Keil 能处理这件事，但脚本和 CI 常常不能。
* 一次**全量重建**会删除并重新创建输出目录；它从不触碰源码。
* 构建日志包含本机的 **Arm 许可证序列号与用户名**。该文件只存在于被忽略的输出目录内。
  未先检查就不要附加或提交原始的 `*.build_log.htm`。
* `DebugConfig/…dbgconf` 与 `RTE/` 由 Keil 生成，但它们属于工程，并且被纳入版本跟踪。

## 未验证

* 作为记录本基线的一部分，没有对硬件执行过任何烧录/编程。
* 一次干净的编译不能证明任何运行期行为 —— 见 [REVIEW_HANDOFF.zh-CN.md](REVIEW_HANDOFF.zh-CN.md)。
