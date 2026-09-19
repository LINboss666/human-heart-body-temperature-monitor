# Upstream Third-Party Code

**English** · [中文](UPSTREAM.zh-CN.md)

This repository vendors two upstream projects instead of fetching them at build
time, because the target is an offline Keil MDK-ARM build with no package
manager. Both are MIT-licensed, and the licence files are shipped unmodified
alongside the code they cover.

## KK_UI

| | |
| --- | --- |
| Origin | https://gitee.com/keysking/kk_ui |
| Branch | `main` |
| Commit integrated | `582c3442ecbc539c1c82a342676b5b2eda69eee0` (tag `v0.1.0` differs only in `README.md`; the C runtime is byte-identical to the tag) |
| Commit date | 2026-09-11 |
| Licence | MIT, holder `Qingdao BaudDance Technology Co., Ltd.` — [LICENSE](../ThirdParty/kk_ui/LICENSE.txt) |
| Integrated | 2026-09-18 |
| Vendored from | `skills/kk-ui-port/assets/kk-ui-runtime/` |
| Placed at | [`ThirdParty/kk_ui/`](../ThirdParty/kk_ui/) — `include/` (3 files) + `src/` (9 files), 3,348 lines |

`kk_ui` is published as an **Agent Skills** distribution: the C runtime is a skill
asset, not a repo-root library. The upstream's own documented procedure
(`skills/kk-ui-port/SKILL.md`) is to copy `include/` and `src/` into the target
project and then treat the copy as project code — including editing it. That is
what was done here; the skill markdown, the 1.2 MB of preview GIFs and the
installer script were **not** vendored.

### Local modifications

Only one file differs from upstream:

| File | Change | Why |
| --- | --- | --- |
| `ThirdParty/kk_ui/include/kk_ui_config.h` | `KK_UI_REFRESH_MODE` default changed from `KK_UI_REFRESH_DMA` to `KK_UI_REFRESH_BLOCKING` | DMA refresh needs I2C1 buffer DMA and its NVIC vector, which the frozen `.ioc` does not enable. Choosing blocking flush avoids a CubeMX revision whose only purpose would be a UI nicety, at the cost of a ~3 ms worst-case blocking flush that the 1 kHz acquisition path tolerates because it is entirely hardware-driven. See [PROTOCOL.md](PROTOCOL.md) and [ARCHITECTURE.md](ARCHITECTURE.md). |

Nothing in `src/` was edited. No API was added. A waveform widget was
**not** invented — upstream has none, and `skills/kk-ui-extend/SKILL.md`
explicitly forbids adding one as a shortcut; the ECG trace is drawn by this
project's own custom page.

## KK_OLED

Mandatory dependency: `kk_ui` calls `kk_oled`'s functions directly and
statically, and provides no display abstraction of its own.

| | |
| --- | --- |
| Origin | https://gitee.com/keysking/kk_oled |
| Branch | `main` |
| Commit integrated | `f01831d63b1d426b629921edaba644732aa29223` |
| Commit date | 2026-09-05 |
| Licence | MIT, holder `青岛波特律动科技有限公司 (Qingdao BaudDance Technology Co., Ltd.)` — [LICENSE](../ThirdParty/kk_oled/LICENSE.txt) |
| Integrated | 2026-09-18 |
| Vendored from | `skills/kk-oled-port/assets/kk-oled-runtime/` |
| Placed at | [`ThirdParty/kk_oled/`](../ThirdParty/kk_oled/) — `include/`, `graphics/` (5 files), `driver/` (2 files), 2,601 lines |

### Local modifications

`driver/kk_oled_driver.c` and `driver/kk_oled_driver.h` are the upstream's declared
hardware adaptation boundary ("当前文件是固定硬件适配边界"), and are the only files
edited. The graphics core, the pixel layout, the clipping logic, the double
buffering and the submit state machine were **not** touched — `driver-contract.md`
marks those as protected. See [KK_UI_NOTES.md](KK_UI_NOTES.md) for the exact diff
and the reasoning.

## Licence obligations actually met

MIT requires only that the copyright notice and permission notice travel with the
software. Accordingly:

* both `LICENSE.txt` files are present, verbatim, next to the code they cover;
* this file records origin, commit, date and every local modification;
* [`docs/KK_UI_NOTES.md`](KK_UI_NOTES.md) records the integration detail;
* neither library's files were re-licensed, and no claim is made that this
  project's own copyright covers them.

## Font data is NOT covered by these MIT licences

This matters more than it looks. Neither upstream ships any font, and the
documented generator (`kk-oled-font`) relies on an online third-party service
whose glyph output is governed by the chosen typeface, not by MIT:

> `LEDFont 生成的字模可能受到所选字体自身许可证约束，不因使用本仓库而转为 MIT 许可。`
> — `kk_oled` README

The fonts committed by this project ([`App/ui/assets/`](../App/ui/assets/)) were
therefore **generated from scratch inside this repository** from a hand-authored
bitmap set by the project author, so no third-party typeface licence is
 implicated. See [KK_UI_NOTES.md](KK_UI_NOTES.md#fonts).
