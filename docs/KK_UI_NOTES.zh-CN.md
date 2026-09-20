# KK_UI / KK_OLED 集成说明

[English](KK_UI_NOTES.md) · **中文**

记录在上游工程中实际发现了什么，以及本固件如何与之符合，这样审阅者不必自己重新推导。
出处与许可证见 [UPSTREAM.zh-CN.md](UPSTREAM.zh-CN.md)。

## 上游究竟是什么

`gitee.com/keysking/kk_ui` 并不是一个独立 C UI 库的仓库。它是一个 **Agent Skills** 发行包，
其 C 运行时是作为*技能资产*携带在 `skills/kk-ui-port/assets/kk-ui-runtime/` 下的。它无法独立
运行：每一次绘制和刷新调用都会进入 `kk_oled`，那是第二个不可或缺的仓库，它拥有帧缓冲、
图形核心以及整个硬件移植层。

两个仓库都不含示例应用、不含演示、不含 Keil 工程，也没有任何 SSD1306/SH1106 代码。
唯一随包携带的显示驱动面向 **CH1116**，挂在 `hi2c1` 上，8 位地址 `0x3D << 1`，列偏移为 2。

因此这里的集成遵循上游自己记录的流程（`skills/kk-ui-port/SKILL.md`，九个步骤，
外加 `references/porting-strategy.md` 与 `references/verification-checklist.md`）：
把运行时拷进工程，把这份拷贝视为工程自有代码，只调整驱动边界。

## 对上游契约的符合性

| 上游要求 | 本工程如何满足 |
| --- | --- |
| 应用先初始化 OLED，再初始化 KK_UI | `ui_app_init()` 在 `KK_UI_Init()` 之前调用 `OLED_Init()`；KK_UI 从不调用 `OLED_Init()` |
| `KK_UI_Update(now_ms, input)` 从单一上下文调用，至少每 10 ms 一次 | 每次 `App_Loop()` 迭代调用一次，约 5 ms；时间是传入的，没有 tick 钩子 |
| 中断只累加原始输入状态 | 按键在主循环里采样；DMA 中断不触碰任何 UI 状态 |
| 页面描述、标签、字体、图标与绑定变量在 UI 生命周期内保持有效 | 全部是文件作用域的 `static`/`const`；动态文本被格式化进静态缓冲，绝不重新绑定到临时对象 |
| 应用不得与 KK_UI 并行清屏或提交帧 | `App/` 中任何位置都不存在 `OLED_Clear()` / `OLED_Update*()` 调用；只有 `KK_UI_Invalidate()` |
| 无动态内存、无控件树、无运行时注册 | 页面是静态表；grep 确认三份代码库中分配调用为零 |
| 缺失的硬件要被报告出来，而不是猜测 | `OLED_Init()` 失败会置 `oled_present=false`，固件继续运行 |

## 偏差及其理由

1. **`KK_UI_REFRESH_MODE` = `KK_UI_REFRESH_BLOCKING`**（上游默认是 DMA）。
   DMA 刷新需要 I2C1 缓冲 DMA 及其中断向量，而被冻结的 `.ioc` 并未使能它们。增加它们就等于
   为一项仅关乎显示的好处去做一次 CubeMX 修订。代价：局部分区刷新是同步的，受脏区宽度限制；
   而双缓冲仍然被分配着，尽管没有任何内容是异步发出的。

2. **HOME 页面禁用，根页面是一个 MENU。**
   HOME 模板要求每个条目配一张 32×32 XBM（`icon_xbm_32x32` 会被校验为非 NULL）。
   为了在一块 64 KB 器件上得到装饰，而要付出编写四张图标位图、外加对它们做验证与许可证审阅
   的代价，而这些东西菜单模板本就已提供。

3. **三个槽位共用一份字体表。**
   `kk_ui_valid_fonts()` 要求 `home_font`、`title_font` 与 `body_font` 非 NULL。三者都指向
   同一个 1113 字节的数组；视觉层次来自布局和反白行。复制这张表会多花 2.2 KB flash。

4. **没有添加波形控件。**
   KK_UI 没有波形控件。`skills/kk-ui-extend/SKILL.md` 声明扩展核心是最后手段，
   且 `实现页面` 的权限并不等于 `扩展核心` 的权限，而 README 自己的指引也是把波形画在自定义
   页面里。因此 ECG 波形由 `KK_UI_CustomOnDraw()` 使用 KK_OLED 原语绘制，而
   `ThirdParty/kk_ui/src/` 与上游字节一致。

5. **在 KK_OLED 中只改动了 `driver/kk_oled_*.{c,h}`**，把三个字面量替换为从
   `App/display/oled_bus.c` 读取的值。图形核心 —— 像素排布、裁剪、双缓冲、提交状态机 ——
   未作改动，因为 `references/driver-contract.md` 把这些标为受保护边界，而
   `kk-oled-port/SKILL.md` 禁止往这些位置猜测控制器参数。

## 上游禁止猜测、因而仍然悬而未决的事项

`skills/kk-oled-port/SKILL.md` 点名了本工程的这些确切未知项，说它们不得从参考驱动或通用模块
默认值填入，包括 CH1116-vs-SSD1306、`0x3C`-vs-`0x3D`、列偏移是 0 还是 2，以及"任何通用初始化
表"，并指出一次 I2C 扫描只能证明某个地址应答了。

据此，`App/display/oled_bus.c` 携带**三份完整的候选方案**，全部标注为未验证，
由 [HARDWARE_TEST_PLAN.zh-CN.md](HARDWARE_TEST_PLAN.zh-CN.md) 的 stage D/E 在台架上判定取舍。
任何地方都没有声称默认那一份是正确的。

## 字体授权：MIT 许可证并不覆盖的那一件事

两个仓库都不携带字形数据，而被记录的生成方式使用的是一个在线服务，对它的输出 kk_oled 明确
说明受所选字体本身的许可证约束，而不是受 MIT 约束。把这类输出提交进公开仓库，本会是这里唯一
一个"随仓携带这些库"解决不了的许可证问题。

因此 `App/ui/ui_fonts.c` 中的 5×7 ASCII 字集是由 `tools/gen_oled_fonts.py` 为本工程自行编写
的，不涉及任何第三方字体。完全没有携带 CJK 字形，这也满足了任务书对大型中文字体表的禁止。

## 实际做过的验证

`tests/host/test_font_format.c` 把 `kk_oled_font.c` **未作修改**地针对生成的数组编译并解码：
78 条断言，覆盖头部解析、每一个可打印码点、自制的字形形状、十二对易混字符，以及对缺失码点的
回退。它发现了三个真实缺陷，而缺陷是靠审阅 C 数组看不出来的：一个过窄的 `advance` 字段、
一个让测试首版空洞通过的 off-by-one，以及 KK_OLED 的"无尾随间隔"宽度规则。

其余关于 UI 的一切 —— 布局、动画、焦点、居中 —— **仅通过编译验证**。因为不存在屏，
所以从未渲染出任何像素。这一点在 [COURSE_REQUIREMENTS.zh-CN.md](COURSE_REQUIREMENTS.zh-CN.md) 中被记录为
待验证，而不是被假定可用。
