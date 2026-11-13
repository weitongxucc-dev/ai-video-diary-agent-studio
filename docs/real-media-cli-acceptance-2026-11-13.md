# 1.5.2 真实媒体与主链路验收记录

记录日期：2026-11-13  
源码工作区：`/Volumes/Dujiangyan-VM/AI视频日记/repositories/main`
分支：`codex/1.5.2-governance-agent-mobile-cli`  
代码提交：`7ec28241`

## 真实成片

验收任务：`task_93d78477016b`  
工作区：`local`  
时间线版本：`timeline_72f61554e7db4c00bf4f57279e91103b`，序号 `29`

API 使用当前源码启动，并显式加载：

```text
AI_VIDEO_DIARY_DOTENV=/Volumes/Dujiangyan-VM/AI视频日记/app-data/default/.env
```

运行时 `VIDEO_OUTPUT_PATH` 为 `/Volumes/Dujiangyan-VM/AI视频日记/outputs`。下载接口返回 `200 video/mp4`，并返回相同的时间线版本和序号响应头。

CLI 使用 0600 私有配置执行：

```text
node scripts/yaohe.mjs --config <private-config> output download task_93d78477016b --file output/acceptance/task_93-cli.mp4
```

结果：退出码 `0`，文件 `15,198,122` 字节，SHA-256：

```text
5842bdbfa2e87fe2a07c82eaf3871ab85faeeaf0aa5deb89d343689cffda67e4
```

`ffprobe` 检查结果：H.264 视频 `1080x1920`、30 fps；AAC 单声道音频、48 kHz；时长 `115.8` 秒。下载文件和数据库登记的 `rendered_video` 摘要一致。

完整 JSON 和下载文件保存在被忽略的本地验收目录：

```text
output/acceptance/real-media-cli-acceptance.json
output/acceptance/task_93-cli.mp4
```

## 浏览器与资源面板

当前源码 Web：`http://127.0.0.1:3100`。真实浏览器验证了：

- `/library?mode=manage` 切换到 `/library?mode=panel&resource=effects` 后，面板随当前 URL 更新；
- 特效分类显示“画面效果、淡入、淡出、暖色调”；
- 页面 console 错误数为 `0`；
- 截图：`output/playwright/asset-panel-effects-rerun.png`。

## 验证结果

- API 定向测试：`50 passed`；
- Web 定向测试：`159 passed`；
- CLI Node 测试：`23 passed`；
- TypeScript：通过；
- Flutter analyze：通过；
- 手机端主链路定向测试：`30 passed`；
- `git diff --check`：通过。

当前环境只发现 macOS 和 Chrome，没有实体手机或平板，因此手机端仍缺少真机、网络切换和系统通知验收。音乐、音效仍是 `catalog_only`，滤镜仍是 `planned`。
