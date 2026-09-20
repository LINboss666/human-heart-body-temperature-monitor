# BUILD — Phase 0 Baseline

**English** · [中文](BUILD.zh-CN.md)

Target IDE: **Keil MDK-ARM (uVision)**. There is no Makefile, CMake or `arm-none-eabi-gcc`
build in this repository, and CubeMX was told to generate an MDK-ARM project only
(`ProjectManager.TargetToolchain = MDK-ARM V5.32`).

## Baseline result

```
0 Error(s), 0 Warning(s).
Program Size: Code=6804  RO-data=328  RW-data=16  ZI-data=2000
Build Time Elapsed: 00:00:04
```

This is a **freshly re-verified** result, not just a transcription of the user's earlier
build. See "Verification history" below for why that distinction matters.

## How to reproduce

1. Install MDK-ARM with device pack `Keil.STM32F1xx_DFP@2.2.0` and `ARM::CMSIS@6.2.0`.
2. Open `MDK-ARM/Human Heart and Body Temperature Monitor.uvprojx`.
3. Select target **Human Heart and Body Temperature Monitor** (the only target).
4. **Project → Build Target** (`F7`), or **Rebuild all target files** (`Ctrl+F7`).

Command line (Windows, from the `MDK-ARM` directory):

```
"C:\Keil_v5\UV4\UV4.exe" -j0 -b "Human Heart and Body Temperature Monitor.uvprojx" -o build.log
```

Exit code `0` means no errors and no warnings.

## Keil target settings (read from the `.uvprojx`, unchanged)

| Setting | Value |
| --- | --- |
| Target name | `Human Heart and Body Temperature Monitor` |
| Device | `STM32F103C8` (STMicroelectronics), pack `Keil.STM32F1xx_DFP.2.2.0` |
| Core | Cortex-M3 (`"Cortex-M3"`) |
| ROM (IROM) | `0x08000000`, size `0x10000` → 64 KB |
| RAM (IRAM) | `0x20000000`, size `0x5000` → 20 KB |
| Preprocessor defines | `USE_HAL_DRIVER, STM32F103xB` |
| Include paths | `../Core/Inc`, `../Drivers/STM32F1xx_HAL_Driver/Inc`, `../Drivers/STM32F1xx_HAL_Driver/Inc/Legacy`, `../Drivers/CMSIS/Device/ST/STM32F1xx/Include`, `../Drivers/CMSIS/Include` |
| Optimisation | Level 3 (`-O3`), time-optimised off (`oTime=0`) |
| Warning level | 2 — Keil's default, "all common warnings". Not level 3, so a suppressed-by-level diagnostic could in principle exist; the rebuild produced no warning text at all. |
| C standard | C99 (`uC99=1`), GNU extensions off (`uGnu=0`), strict ANS C off (`Strict=0`) |
| Interwork | enabled (`interw=1`) — ARM/Thumb interworking sections |
| MicroLIB | no `useUlib` element present in the target's `Cads` block, i.e. standard C library |
| Scatter file | none supplied — `umfTarg=1`, i.e. ROM/RAM taken from the Target dialog and `.sct` auto-generated |
| Hex file | `CreateHexFile=1`, output next to the `.axf` |
| Output directory | `Human Heart and Body Temperature Monitor\` (space-containing path; see gotchas) |
| Linker/LTO | AC5 (ARMCC V5.06), no LTO |

## Toolchain actually used for the verification build

```
MDK-ARM Plus        Version 5.43.0.0
Toolchain path      C:\Keil_v5\ARM\ARMCC\Bin
Armcc/Armasm/ArmLink V5.06 update 5 (build 528)
CPU DLL             SARMCM3.DLL V5.43.0.0
Target DLL          STLink\ST-LINKIII-KEIL_SWO.dll V3.3.1.0
CMSIS               ARM::CMSIS 6.2.0 (CORE component 6.1.1)
Device DFP          Keil.STM32F1xx_DFP 2.2.0
```

Note the `.ioc` was authored with **CubeMX 6.17.0**, which emits MDK projects for
**MDK-ARM V5.32**; the local IDE is **5.43**. The generated project opens and builds
cleanly on 5.43 with AC5, so this version drift is cosmetic. It is recorded because a
reviewer on a different MDK version should know which combination is proven.

## Source inventory the target compiles

31 files total:

* `startup_stm32f103xb.s`
* 11 files from `Core/Src/` (`main`, `gpio`, `adc`, `dma`, `i2c`, `rtc`, `tim`, `usart`, `stm32f1xx_it`, `stm32f1xx_hal_msp`, `system_stm32f1xx`)
* 19 files from `Drivers/STM32F1xx_HAL_Driver/Src/`
* 1 CMSIS component resolved through RTE (`ARM::CMSIS:CORE`)

Groups: `Application/MDK-ARM`, `Application/User/Core`, `Drivers/STM32F1xx_HAL_Driver`,
`Drivers/CMSIS`, `::CMSIS`.

> **Repository-size observation (not acted on).** `Drivers/` is ~91 MB of the ~98 MB tree,
> and most of it — `Drivers/CMSIS/Lib` (35 MB of prebuilt `arm_*_math.lib`),
> `Drivers/CMSIS/DSP` (13 MB), `Drivers/CMSIS/NN`, `Core_A`, `RTOS`, `RTOS2`, `examples`
> — is copied in by CubeMX but is **not** in the compile list above. Phase 0 keeps
> `Drivers/` intact as instructed; shrinking it is a candidate for a separate, explicit
> commit later, not a "while we're here" change.

## Verification history — read this before trusting any "0 errors" claim

The file timestamps in this workspace tell a more precise story than "it built fine":

| Time (2026-09-18) | Event | Evidence |
| --- | --- | --- |
| 21:15 | CubeMX created the project skeleton | directory mtimes |
| **21:17** | **User's Keil build → `0 Error(s), 0 Warning(s)`, `Code=6748`** | original `*.build_log.htm` |
| **21:18:39 – 21:18:58** | **CubeMX regenerated the project** — `.ioc`, all of `Core/Src`+`Core/Inc`, `.mxproject`, `.uvprojx`, `.uvoptx` | file mtimes |
| 21:34 | Clean rebuild (`UV4 -j0 -r`) from the **current** sources → `0 Error(s), 0 Warning(s)`, `Code=6804` | this session |

**Consequence:** the build the user originally saw was against the generation *before*
the last CubeMX write, so its `Code=6748` does not describe the committed tree.
The committed baseline is described by the **21:34** rebuild: `0 Error(s), 0 Warning(s)`,
`Code=6804`. The 56-byte delta is consistent with regenerated sources differing slightly
from the earlier pass; no source was edited by hand in either direction.

Both facts are reported here rather than smoothed over, because "the baseline builds clean"
must mean *this exact tree* builds clean. It does, and that has now been proven from scratch.

The rebuild wrote only into `MDK-ARM/Human Heart and Body Temperature Monitor/`, which is
git-ignored. Verified afterwards: no file under `Core/` or `Drivers/`, and neither the
`.ioc`, `.mxproject`, `.uvprojx` nor `.uvoptx`, changed after 21:19.

## Gotchas

* **The project name contains spaces** (folder, `.ioc`, `.uvprojx`, output directory).
  Command-line builds must quote paths. Keil handles it, but scripts and CI often do not.
* A **rebuild** deletes and recreates the output directory; it never touches sources.
* The build log contains the local machine's **Arm license serial number and user name**.
  That file lives only inside the ignored output directory. Do not attach or commit a raw
  `*.build_log.htm` without checking it first.
* `DebugConfig/…dbgconf` and `RTE/` are Keil-generated but are part of the project and
  are tracked.

## Not verified

* No flash/programming to hardware has been performed as part of documenting this baseline.
* No run-time behaviour is proven by a clean compile — see [REVIEW_HANDOFF.md](REVIEW_HANDOFF.md).
