# 2026-10-08 桌面设计参考资产

唯一规则归属[DESIGN.md](../../../DESIGN.md)。本目录提供不同职责的参考资产，不维护第二份设计规范。

## 当前入口与适用范围

- [完整圆角与叠加v2](floating-sidecards-v2/index.html)：中间栏、对象详情、配置、字段选择和局部确认的当前参考。依据DESIGN.md第2节、第4.2节及第7节实施。
- [全页内容原型](index.html)：模块内容、页面组织和集合形态参考；其旧中栏、详情侧栏、配置/选择尺寸和普通确认位置已被v2替代，不能据此施工。

直接用浏览器打开上述HTML即可，资源已内嵌，无需安装或启动服务。主控收录时未执行页面、构建程序或浏览器检查脚本。

## 来源与保真

同名ZIP曾被用户更新，必须用内容hash区分，不以文件名判断版本。

| 资产 | 来源ZIP SHA256 | 收录HTML SHA256 |
| --- | --- | --- |
| 全页内容 | eb4e352f3dab9f9c8d290f8129b465370e9215cdf8caf818386caa20070aeb22 | 190d14ef83e751a3cb74bb65b19e0e2856fba5daf8bdcb205cab3969c626b5e4 |
| 侧边浮卡v2 | e44effe5c052e5b0a3b49e8facd45dbffbe3e656b29253e52e030cba00d88d0b | a416b02c341398181a4fefd685ba834aee3bbd0a2efbd8c2dce1f97a85926f1d |

HTML与对应截图均从交接包逐字节收录。全页HTML在两份包中相同；v2来自新包的`designs/floating-sidecards-stack/`。Lucide许可分别保留于[全页许可](Lucide-LICENSE.txt)和[v2许可](floating-sidecards-v2/Lucide-LICENSE.txt)。

原包的候选说明、第一版浮卡、研究档案与构建程序不另作为活动规范复制。已被替代的旧详情/确认截图从活动资产退役，通过Git历史追溯。

## 定向截图

- 全页内容：[知识库](screenshots/knowledge-full.png)、[设置](screenshots/settings.png)。其中出现的旧中栏不作为宽度依据。
- v2：[三层叠加1440](floating-sidecards-v2/screenshots/stack-1440.png)、[三层叠加1024](floating-sidecards-v2/screenshots/stack-1024.png)。
- v2：[右侧局部确认](floating-sidecards-v2/screenshots/confirm-right-1440.png)、[镜头编辑1280](floating-sidecards-v2/screenshots/shot-1280.png)。

这些是原型参考截图，不是本次生产验收。v2主要示范浅色，生产暗色按DESIGN.md语义token映射。媒体占位、演示身份、模拟操作和原包浏览器记录不证明实际模型、存储、权限或发布成功。
