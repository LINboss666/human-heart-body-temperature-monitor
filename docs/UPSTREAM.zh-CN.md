# 上游第三方代码

[English](UPSTREAM.md) · **中文**

本仓库以 vendor 方式内置了两个上游项目，而不是在构建时再去获取，因为目标是一个离线的
Keil MDK-ARM 构建，没有包管理器。两者均为 MIT 授权，且授权文件未经修改地随其所覆盖的
代码一起提供。

## KK_UI

| | |
| --- | --- |
| 来源 | https://gitee.com/keysking/kk_ui |
| 分支 | `main` |
| 集成的提交 | `582c3442ecbc539c1c82a342676b5b2eda69eee0`（标签 `v0.1.0` 仅在 `README.md` 上有差异；C 运行时与该标签逐字节相同） |
| 提交日期 | 2026-09-11 |
| 授权 | MIT，持有者 `Qingdao BaudDance Technology Co., Ltd.` — [LICENSE](../ThirdParty/kk_ui/LICENSE.txt) |
| 集成日期 | 2026-09-18 |
| vendor 来源 | `skills/kk-ui-port/assets/kk-ui-runtime/` |
| 放置位置 | [`ThirdParty/kk_ui/`](../ThirdParty/kk_ui/) — `include/`（3 个文件）+ `src/`（9 个文件），3,348 行 |

`kk_ui` 是以 **Agent Skills** 分发包的形式发布的：C 运行时是一个 skill 资产，而不是
仓库根目录下的库。上游自己记录的流程（`skills/kk-ui-port/SKILL.md`）就是把 `include/`
和 `src/` 复制进目标工程，然后把这份副本当作工程代码来对待 —— 包括修改它。这里做的
正是这件事；skill 的 markdown、那 1.2 MB 的预览 GIF 以及安装脚本都**没有**被以 vendor
方式引入。

### 本地修改

只有一个文件与上游不同：

| 文件 | 改动 | 原因 |
| --- | --- | --- |
| `ThirdParty/kk_ui/include/kk_ui_config.h` | `KK_UI_REFRESH_MODE` 的默认值由 `KK_UI_REFRESH_DMA` 改为 `KK_UI_REFRESH_BLOCKING` | DMA 刷新需要 I2C1 的缓冲区 DMA 及其 NVIC 向量，而被冻结的 `.ioc` 并没有使能它们。选择阻塞式刷新，可以避免一次 CubeMX 修订 —— 那种修订唯一的目的只是 UI 上的小事 —— 代价是最坏情况下约 3 ms 的阻塞刷新，而 1 kHz 的采集路径能够容忍它，因为它完全由硬件驱动。见 [PROTOCOL.zh-CN.md](PROTOCOL.zh-CN.md) 与 [ARCHITECTURE.zh-CN.md](ARCHITECTURE.zh-CN.md)。 |

`src/` 中没有任何内容被编辑。没有新增任何 API。波形控件**没有**被凭空发明 —— 上游没有
这种东西，而 `skills/kk-ui-extend/SKILL.md` 明确禁止以走捷径的方式添加一个；ECG 波形是
由本工程自己的自定义页面绘制的。

## KK_OLED

强制性依赖：`kk_ui` 直接且静态地调用 `kk_oled` 的函数，并且自身不提供任何显示抽象。

| | |
| --- | --- |
| 来源 | https://gitee.com/keysking/kk_oled |
| 分支 | `main` |
| 集成的提交 | `f01831d63b1d426b629921edaba644732aa29223` |
| 提交日期 | 2026-09-05 |
| 授权 | MIT，持有者 `青岛波特律动科技有限公司 (Qingdao BaudDance Technology Co., Ltd.)` — [LICENSE](../ThirdParty/kk_oled/LICENSE.txt) |
| 集成日期 | 2026-09-18 |
| vendor 来源 | `skills/kk-oled-port/assets/kk-oled-runtime/` |
| 放置位置 | [`ThirdParty/kk_oled/`](../ThirdParty/kk_oled/) — `include/`、`graphics/`（5 个文件）、`driver/`（2 个文件），2,601 行 |

### 本地修改

`driver/kk_oled_driver.c` 和 `driver/kk_oled_driver.h` 是上游声明的硬件适配边界
（"当前文件是固定硬件适配边界"），也是唯一被编辑的文件。图形核心、像素布局、裁剪逻辑、
双缓冲以及提交状态机都**没有**被改动 —— `driver-contract.md` 把这些标为受保护。确切
的 diff 与推理见 [KK_UI_NOTES.zh-CN.md](KK_UI_NOTES.zh-CN.md)。

## 实际履行了的授权义务

MIT 只要求版权声明与许可声明随软件一起分发。因此：

* 两个 `LICENSE.txt` 都在，逐字未改，就在它们所覆盖的代码旁边；
* 本文件记录了来源、提交、日期以及每一处本地修改；
* [`docs/KK_UI_NOTES.md`](KK_UI_NOTES.zh-CN.md) 记录了集成的细节；
* 两个库的文件都没有被重新授权，也没有任何声明称本工程自身的版权覆盖它们。

## 字体数据不在这些 MIT 授权的覆盖范围内

这一点比看上去更重要。两个上游都不携带任何字体，而已记录的生成器（`kk-oled-font`）
依赖一个在线第三方服务，该服务输出的字模受所选字体自身的约束，而不是受 MIT 约束：

> `LEDFont 生成的字模可能受到所选字体自身许可证约束，不因使用本仓库而转为 MIT 许可。`
> — `kk_oled` README

因此本工程提交的字体（[`App/ui/assets/`](../App/ui/assets/)）是**在本仓库内部从零
生成的**，源自工程作者手工编写的一套点阵位图，所以不牵涉任何第三方字体授权。见
[KK_UI_NOTES.md](KK_UI_NOTES.md#fonts)。
