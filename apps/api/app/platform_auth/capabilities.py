"""平台能力登记表：扫码授权 + 草稿箱写入，两个维度的诚实结论。

`scan_oauth`/`long_term_token` = 授权维度；`draft_box` = 能否程序化写入创作后台草稿箱。
查证结论决定本包能做到什么程度：草稿箱这格，微信视频号=无此能力，抖音/小红书=未查证。
"""

from __future__ import annotations

from dataclasses import dataclass

PLATFORMS = ("douyin", "wechat", "xiaohongshu")


@dataclass(frozen=True)
class PlatformCapability:
    platform: str
    label: str
    # 授权维度
    scan_oauth: bool
    long_term_token: bool
    # token 能做什么：video_publish=发布视频 / identity_only=仅身份 / unknown=未查证
    publish_scope: str
    # 草稿箱维度：draft_api=有草稿接口 / none=无此能力 / unknown=未查证
    draft_box: str
    requirement: str
    verified: bool
    source: str


CAPABILITIES: dict[str, PlatformCapability] = {
    "douyin": PlatformCapability(
        platform="douyin",
        label="抖音",
        scan_oauth=True,
        long_term_token=True,
        publish_scope="video_publish",
        draft_box="unknown",
        requirement=(
            "OAuth authorization_code + refresh_token 成立；"
            "视频发布/草稿箱是否对普通开发者开放（企业资质/开发者账号/应用审核）未查证，"
            "开放平台内容能力导航未见「保存草稿」条目。"
        ),
        verified=True,
        source="developer.open-douyin.com 授权获取 access_token + 内容能力导航",
    ),
    "wechat": PlatformCapability(
        platform="wechat",
        label="微信视频号",
        scan_oauth=True,
        long_term_token=False,
        publish_scope="identity_only",
        draft_box="none",
        requirement=(
            "微信登录 OAuth（扫码）成立但仅返回身份 token；"
            "视频号助手 API 仅直播/橱窗/留资/带货数据/本地生活，无发布短视频、无草稿箱接口。"
        ),
        verified=True,
        source="developers.weixin.qq.com 网站应用微信登录 + /doc/channels/api/channels/",
    ),
    "xiaohongshu": PlatformCapability(
        platform="xiaohongshu",
        label="小红书",
        scan_oauth=False,
        long_term_token=False,
        publish_scope="unknown",
        draft_box="unknown",
        requirement="未查证（官方文档页三次抓取均返回空）。",
        verified=False,
        source="open.xiaohongshu.com（抓取失败，未查证）",
    ),
}


def capability_for(platform: str) -> PlatformCapability | None:
    return CAPABILITIES.get(platform)
