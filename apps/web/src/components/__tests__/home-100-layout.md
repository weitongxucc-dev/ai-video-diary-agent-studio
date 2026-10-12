# 首页100项隔离布局fixture

仅展示内存中的合成记录，不是业务任务，不连接API、不写数据库，也不增加产品QA路由。页面复用当前 HomeWorkspace 与当前 globals/modern-overrides；明确标注“100项隔离布局 fixture · 非业务记录”。链接导航被阻止，接口请求被拒绝；只允许观察布局、本地搜索/过滤/分页、词云、零趋势、主题及浮卡。

已有依赖的独立运行入口（由主线程选择是否启动；无需安装或产品build）：

```sh
cd /Volumes/Dujiangyan-VM/AI视频日记/repositories/desktop-v32/apps/web
node src/components/__tests__/home-100-layout.mjs
```

浏览器打开 http://127.0.0.1:4309/src/components/__tests__/home-100-layout.html 。固定 localhost4309/strictPort，不影响root4308；退出该终端可关闭fixture服务。

数据为40待处理、30运行、30完成。检查三个内部列表分别滚动、外层高度不随100项增长；切列表应只有25项与4页、搜索后回到已读集合对应页。初始有真实组件绘制的合成全零7天坐标图，7/30仅切换合成日期；词云频数100/48/20/12/8/4/2/1用于观察字号与排布，不能据其截图宣称产品数据或实际任务通过。

本轮未由子线程启动fixture服务或截图；root统一用它补100项布局的真实浏览器几何/滚动检查。单测入口：

```sh
npx vitest run src/components/__tests__/HomeWorkspace.test.tsx -t '100 layout-only'
```

Fixture本身不覆盖原生Electron、真实API权限/计数、真实模型、工作区切换、业务任务分页或IME系统候选。本轮生产接口与数据均保持原样。
