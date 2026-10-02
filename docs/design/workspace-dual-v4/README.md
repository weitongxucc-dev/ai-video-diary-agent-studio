# 当前认可的双身份交互原型

主账号：[owner.html](owner.html)；成员：[member.html](member.html)。视觉与交互基线的适用规则见 [DESIGN.md 第5.10节](../../../DESIGN.md#510-当前视觉与交互基线)。

本目录七个运行文件与用户确认的本地 `http://127.0.0.1:4327/workspace-dual-v4/` 页面逐字节核对后原样收录。只收录可运行来源，不复制旧截图、旧任务报告或旧原型版本。

在仓库根目录运行：

```sh
python3 -m http.server 4328 --bind 127.0.0.1 --directory docs/design
```

打开 `http://127.0.0.1:4328/workspace-dual-v4/owner.html`。本地原有4327服务不需要改动。原型包含演示数据和模拟交互，不连接真实文件、模型、发布或权限服务；不能作为生产功能完成的证据。
