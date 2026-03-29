"""
100条系统测试数据构建脚本（v3）
适配新逻辑：returned 替代 reject / 取消提前终止 / 违规分级 / 品牌保护修复
新增60条：文案边界/资质深化/落地页深化/图片深化/视频深化/平台深化/一致性深化/综合深化
"""

import asyncio
import httpx
import json
import sys
import time
from datetime import datetime

BASE_URL = "http://localhost:8000"
API_KEY = "test-key-001"

# ============================================================
# TEST CASES — 100条，覆盖15个模块（A-G原有 + I-P新增）
# ============================================================

TEST_CASES = [

    # ═══ 模块A：纯文案（8条）═══

    {
        "case_id": "T-A01", "desc": "【文案】金融多项违规-blocking全命中",
        "expected": "returned", "verify_dims": [],
        "data": {
            "request_id": "T-A01", "advertiser_id": "adv-fin-001",
            "ad_category": "finance", "creative_type": "text",
            "content": {
                "title": "全球最安全理财，保本保息零风险",
                "description": "年化12%，本金100%安全，保证兑付，50万人获益",
                "cta_text": "立即投资"
            }
        }
    },
    {
        "case_id": "T-A02", "desc": "【文案】医疗疗效承诺+治愈宣称",
        "expected": "returned", "verify_dims": [],
        "data": {
            "request_id": "T-A02", "advertiser_id": "adv-health-001",
            "ad_category": "health", "creative_type": "text",
            "content": {
                "title": "糖尿病根治神器，三个月彻底康复",
                "description": "有效率98%，永不复发，替代胰岛素",
                "cta_text": "立即购买"
            }
        }
    },
    {
        "case_id": "T-A03", "desc": "【文案】伪造系统告警诱导安装",
        "expected": "returned", "verify_dims": [],
        "data": {
            "request_id": "T-A03", "advertiser_id": "adv-tool-001",
            "ad_category": "tool_app", "creative_type": "text",
            "content": {
                "title": "系统检测到您的手机存在严重安全威胁",
                "description": "已感染3个病毒，立即安装修复",
                "cta_text": "立即修复"
            }
        }
    },
    {
        "case_id": "T-A04", "desc": "【文案】游戏充值返利+无版号",
        "expected": "returned", "verify_dims": ["qualification"],
        "data": {
            "request_id": "T-A04", "advertiser_id": "adv-game-001",
            "ad_category": "game", "creative_type": "text",
            "content": {
                "title": "充值1000送3000，月入万元不是梦",
                "description": "公会瓜分百万奖池，躺赚首选手游",
                "cta_text": "立即充值"
            }
        }
    },
    {
        "case_id": "T-A05", "desc": "【文案】对抗性-谐音符号绕过",
        "expected": "returned", "verify_dims": [],
        "data": {
            "request_id": "T-A05", "advertiser_id": "adv-tool-002",
            "ad_category": "tool_app", "creative_type": "text",
            "content": {
                "title": "全球zui好用工具，Di\u2460名体验",
                "description": "保.证.有.效，w\u00e9i y\u012b采用AI技术",
                "cta_text": "下载"
            }
        }
    },
    {
        "case_id": "T-A06", "desc": "【文案】教育虚假承诺",
        "expected": "returned", "verify_dims": [],
        "data": {
            "request_id": "T-A06", "advertiser_id": "adv-edu-001",
            "ad_category": "education", "creative_type": "text",
            "content": {
                "title": "全国第一考研机构，100%上岸保障",
                "description": "通过率99%，不过全额退款",
                "cta_text": "立即报名"
            }
        }
    },
    {
        "case_id": "T-A07", "desc": "【文案】边界-效果宣传模糊",
        "expected": "review", "verify_dims": [],
        "data": {
            "request_id": "T-A07", "advertiser_id": "adv-ecom-001",
            "ad_category": "ecommerce", "creative_type": "text",
            "content": {
                "title": "超神奇护肤品，用了都说好",
                "description": "效果显著，深受用户好评，回购率超高",
                "cta_text": "查看详情"
            }
        }
    },
    {
        "case_id": "T-A08", "desc": "【文案】合规-工具类正常广告",
        "expected": "pass", "verify_dims": [],
        "data": {
            "request_id": "T-A08", "advertiser_id": "adv-tool-003",
            "ad_category": "tool_app", "creative_type": "text",
            "content": {
                "title": "手机清理助手",
                "description": "一键清理缓存，释放存储空间，让手机更流畅",
                "cta_text": "免费下载"
            }
        }
    },

    # ═══ 模块B：图片审核（6条）═══

    {
        "case_id": "T-B01", "desc": "【图片】图文一致-合规",
        "expected": "pass", "verify_dims": ["image_safety"],
        "data": {
            "request_id": "T-B01", "advertiser_id": "adv-tool-004",
            "ad_category": "tool_app", "creative_type": "mixed",
            "content": {
                "title": "百度搜索App",
                "description": "随时随地搜索，信息触手可得",
                "cta_text": "立即使用",
                "image_urls": ["https://www.baidu.com/img/PCtm_d9c8750bed0b3c7d089fa7d55720d6cf.png"]
            }
        }
    },
    {
        "case_id": "T-B02", "desc": "【图片】品牌冲突-vivo文案+百度图",
        "expected": "review", "verify_dims": ["image_safety", "consistency"],
        "data": {
            "request_id": "T-B02", "advertiser_id": "adv-tool-005",
            "ad_category": "tool_app", "creative_type": "mixed",
            "content": {
                "title": "vivo手机管家Pro",
                "description": "智能管理手机，保护隐私安全",
                "cta_text": "免费下载",
                "image_urls": ["https://www.baidu.com/img/PCtm_d9c8750bed0b3c7d089fa7d55720d6cf.png"]
            }
        }
    },
    {
        "case_id": "T-B03", "desc": "【图片】违规文案+图片(验证全维度不提前终止)",
        "expected": "returned", "verify_dims": ["text_violation", "image_safety"],
        "data": {
            "request_id": "T-B03", "advertiser_id": "adv-health-002",
            "ad_category": "health", "creative_type": "mixed",
            "content": {
                "title": "最好的保健品，专治百病",
                "description": "根治糖尿病，保证有效",
                "cta_text": "立即抢购",
                "image_urls": ["https://www.w3schools.com/css/img_5terre.jpg"]
            }
        }
    },
    {
        "case_id": "T-B04", "desc": "【图片】多图-合规",
        "expected": "pass", "verify_dims": ["image_safety"],
        "data": {
            "request_id": "T-B04", "advertiser_id": "adv-ecom-002",
            "ad_category": "ecommerce", "creative_type": "mixed",
            "content": {
                "title": "精选好物特惠",
                "description": "品质生活，优选商品，新用户立减20元",
                "cta_text": "立即选购",
                "image_urls": [
                    "https://www.baidu.com/img/PCtm_d9c8750bed0b3c7d089fa7d55720d6cf.png",
                    "https://www.w3schools.com/css/img_5terre.jpg"
                ]
            }
        }
    },
    {
        "case_id": "T-B05", "desc": "【图片】游戏合规+有版号",
        "expected": "pass", "verify_dims": ["image_safety", "qualification"],
        "data": {
            "request_id": "T-B05", "advertiser_id": "adv-game-002",
            "ad_category": "game", "creative_type": "mixed",
            "content": {
                "title": "三国策略手游",
                "description": "重现三国，与好友共创霸业",
                "cta_text": "立即下载",
                "image_urls": ["https://www.w3schools.com/css/img_5terre.jpg"]
            },
            "advertiser_qualification_ids": ["ISBN978-7-100-00001"]
        }
    },
    {
        "case_id": "T-B06", "desc": "【图片】产品类别错位",
        "expected": "review", "verify_dims": ["consistency"],
        "data": {
            "request_id": "T-B06", "advertiser_id": "adv-ecom-003",
            "ad_category": "ecommerce", "creative_type": "mixed",
            "content": {
                "title": "健康食品电商平台",
                "description": "精选有机食材，健康生活从饮食开始",
                "cta_text": "立即选购",
                "image_urls": ["https://www.baidu.com/img/PCtm_d9c8750bed0b3c7d089fa7d55720d6cf.png"]
            }
        }
    },

    # ═══ 模块C：落地页审核（6条）═══

    {
        "case_id": "T-C01", "desc": "【落地页】一致-合规",
        "expected": "pass", "verify_dims": ["landing_page"],
        "data": {
            "request_id": "T-C01", "advertiser_id": "adv-tool-006",
            "ad_category": "tool_app", "creative_type": "text",
            "content": {
                "title": "百度App",
                "description": "智能搜索，语音识别，新闻资讯",
                "cta_text": "立即下载",
                "landing_page_url": "https://www.baidu.com"
            }
        }
    },
    {
        "case_id": "T-C02", "desc": "【落地页】免费承诺+收费页面",
        "expected": "returned", "verify_dims": ["landing_page"],
        "data": {
            "request_id": "T-C02", "advertiser_id": "adv-tool-007",
            "ad_category": "tool_app", "creative_type": "text",
            "content": {
                "title": "完全免费永久使用，零费用解锁全部功能",
                "description": "无需付费，所有高级功能免费开放",
                "cta_text": "免费获取",
                "landing_page_url": "https://www.apple.com/app-store/"
            }
        }
    },
    {
        "case_id": "T-C03", "desc": "【落地页】404降级不拉分(验证降级逻辑)",
        "expected": "returned", "verify_dims": [],
        "data": {
            "request_id": "T-C03", "advertiser_id": "adv-game-003",
            "ad_category": "game", "creative_type": "text",
            "content": {
                "title": "全球最强手游，充值返利月入万元",
                "description": "充值1000返3000，保证收益",
                "cta_text": "立即充值",
                "landing_page_url": "https://this-domain-404.com/page"
            }
        }
    },
    {
        "case_id": "T-C04", "desc": "【落地页】产品不符",
        "expected": "review", "verify_dims": ["landing_page"],
        "data": {
            "request_id": "T-C04", "advertiser_id": "adv-ecom-004",
            "ad_category": "ecommerce", "creative_type": "text",
            "content": {
                "title": "超值手机壳特惠，买一送三",
                "description": "精选手机壳，限时特价",
                "cta_text": "立即抢购",
                "landing_page_url": "https://www.apple.com"
            }
        }
    },
    {
        "case_id": "T-C05", "desc": "【落地页】教育平台-合规",
        "expected": "pass", "verify_dims": ["landing_page"],
        "data": {
            "request_id": "T-C05", "advertiser_id": "adv-edu-002",
            "ad_category": "education", "creative_type": "text",
            "content": {
                "title": "W3Schools编程学习",
                "description": "免费学习HTML、CSS、JavaScript",
                "cta_text": "免费开始",
                "landing_page_url": "https://www.w3schools.com"
            }
        }
    },
    {
        "case_id": "T-C06", "desc": "【落地页】违规文案+落地页(验证全维度检测)",
        "expected": "returned", "verify_dims": ["text_violation", "landing_page"],
        "data": {
            "request_id": "T-C06", "advertiser_id": "adv-edu-003",
            "ad_category": "education", "creative_type": "text",
            "content": {
                "title": "最好的在线学习平台，第一名的教学质量",
                "description": "唯一获得国际认证的编程课程",
                "cta_text": "免费试学",
                "landing_page_url": "https://www.w3schools.com"
            }
        }
    },

    # ═══ 模块D：资质审核（5条）═══

    {
        "case_id": "T-D01", "desc": "【资质】游戏无版号",
        "expected": "returned", "verify_dims": ["qualification"],
        "data": {
            "request_id": "T-D01", "advertiser_id": "adv-game-004",
            "ad_category": "game", "creative_type": "text",
            "content": {
                "title": "全新角色扮演手游",
                "description": "精美3D画面，百万玩家已加入",
                "cta_text": "免费下载"
            }
        }
    },
    {
        "case_id": "T-D02", "desc": "【资质】游戏有版号-合规",
        "expected": "pass", "verify_dims": [],
        "data": {
            "request_id": "T-D02", "advertiser_id": "adv-game-005",
            "ad_category": "game", "creative_type": "text",
            "content": {
                "title": "经典消除手游",
                "description": "轻松益智，随时畅玩",
                "cta_text": "立即下载"
            },
            "advertiser_qualification_ids": ["ISBN978-7-100-12345"]
        }
    },
    {
        "case_id": "T-D03", "desc": "【资质】金融无牌照",
        "expected": "returned", "verify_dims": ["qualification"],
        "data": {
            "request_id": "T-D03", "advertiser_id": "adv-fin-002",
            "ad_category": "finance", "creative_type": "text",
            "content": {
                "title": "专业理财顾问服务",
                "description": "精准选股，助您财富增值。投资有风险，入市需谨慎。",
                "cta_text": "免费咨询"
            }
        }
    },
    {
        "case_id": "T-D04", "desc": "【资质】医疗无批准文号",
        "expected": "returned", "verify_dims": ["qualification"],
        "data": {
            "request_id": "T-D04", "advertiser_id": "adv-health-003",
            "ad_category": "health", "creative_type": "text",
            "content": {
                "title": "近视矫正仪",
                "description": "专利技术，修复视网膜，无需手术",
                "cta_text": "免费体验"
            }
        }
    },
    {
        "case_id": "T-D05", "desc": "【资质】工具类无需资质-合规",
        "expected": "pass", "verify_dims": [],
        "data": {
            "request_id": "T-D05", "advertiser_id": "adv-tool-008",
            "ad_category": "tool_app", "creative_type": "text",
            "content": {
                "title": "文件管理工具",
                "description": "智能整理手机文件，快速查找文档",
                "cta_text": "免费下载"
            }
        }
    },

    # ═══ 模块E：视频审核（4条，异步）═══

    {
        "case_id": "T-E01", "desc": "【视频】合规文案+合规视频",
        "expected": "pass", "verify_dims": ["image_safety"],
        "is_async": True,
        "data": {
            "request_id": "T-E01", "advertiser_id": "adv-tool-009",
            "ad_category": "tool_app", "creative_type": "video",
            "content": {
                "title": "手机清理工具演示",
                "description": "简单三步清理垃圾，释放存储空间",
                "cta_text": "免费下载",
                "video_url": "https://www.w3schools.com/html/mov_bbb.mp4"
            }
        }
    },
    {
        "case_id": "T-E02", "desc": "【视频】违规文案(验证不提前终止)",
        "expected": "returned", "verify_dims": ["text_violation", "image_safety"],
        "is_async": True,
        "data": {
            "request_id": "T-E02", "advertiser_id": "adv-game-006",
            "ad_category": "game", "creative_type": "video",
            "content": {
                "title": "全球最强游戏，充值返利月入万元",
                "description": "充值1000返3000，全服第一",
                "cta_text": "立即充值",
                "video_url": "https://www.w3schools.com/html/mov_bbb.mp4"
            },
            "advertiser_qualification_ids": ["ISBN978-7-100-00001"]
        }
    },
    {
        "case_id": "T-E03", "desc": "【视频+图片】混合合规",
        "expected": "pass", "verify_dims": ["image_safety"],
        "is_async": True,
        "data": {
            "request_id": "T-E03", "advertiser_id": "adv-ecom-005",
            "ad_category": "ecommerce", "creative_type": "mixed",
            "content": {
                "title": "夏日特惠活动",
                "description": "精选好物，新用户立减50元",
                "cta_text": "立即抢购",
                "image_urls": ["https://www.w3schools.com/css/img_5terre.jpg"],
                "video_url": "https://www.w3schools.com/html/mov_bbb.mp4"
            }
        }
    },
    {
        "case_id": "T-E04", "desc": "【视频+落地页】全要素合规",
        "expected": "pass", "verify_dims": ["image_safety", "landing_page"],
        "is_async": True,
        "data": {
            "request_id": "T-E04", "advertiser_id": "adv-edu-004",
            "ad_category": "education", "creative_type": "video",
            "content": {
                "title": "W3Schools学习平台",
                "description": "免费学习编程，随时随地",
                "cta_text": "开始学习",
                "video_url": "https://www.w3schools.com/html/mov_bbb.mp4",
                "landing_page_url": "https://www.w3schools.com"
            }
        }
    },

    # ═══ 模块F：平台规范（5条）═══

    {
        "case_id": "T-F01", "desc": "【平台】品牌正常提及-合规",
        "expected": "pass", "verify_dims": ["platform_rule"],
        "data": {
            "request_id": "T-F01", "advertiser_id": "adv-tool-010",
            "ad_category": "tool_app", "creative_type": "text",
            "content": {
                "title": "vivo手机管家",
                "description": "专为vivo手机设计，智能管理保护隐私",
                "cta_text": "立即使用"
            }
        }
    },
    {
        "case_id": "T-F02", "desc": "【平台】品牌冒用-违规",
        "expected": "returned", "verify_dims": ["platform_rule"],
        "data": {
            "request_id": "T-F02", "advertiser_id": "adv-tool-011",
            "ad_category": "tool_app", "creative_type": "text",
            "content": {
                "title": "vivo官方推荐，最强清理工具",
                "description": "vivo认证产品，官方合作伙伴独家提供",
                "cta_text": "立即下载"
            }
        }
    },
    {
        "case_id": "T-F03", "desc": "【平台】竞品攻击-直接点名",
        "expected": "returned", "verify_dims": ["platform_rule"],
        "data": {
            "request_id": "T-F03", "advertiser_id": "adv-tool-012",
            "ad_category": "tool_app", "creative_type": "text",
            "content": {
                "title": "比华为手机管家更好用，完爆小米清理",
                "description": "同类产品中遥遥领先，OPPO用户都在换",
                "cta_text": "立即下载"
            }
        }
    },
    {
        "case_id": "T-F04", "desc": "【平台】竞品攻击-隐性表述",
        "expected": "review", "verify_dims": [],
        "data": {
            "request_id": "T-F04", "advertiser_id": "adv-tool-013",
            "ad_category": "tool_app", "creative_type": "text",
            "content": {
                "title": "友商品牌做不到的，我们都能做到",
                "description": "传统厂商已落后，新一代技术引领行业",
                "cta_text": "立即体验"
            }
        }
    },
    {
        "case_id": "T-F05", "desc": "【平台】合规-正常宣传",
        "expected": "pass", "verify_dims": [],
        "data": {
            "request_id": "T-F05", "advertiser_id": "adv-tool-014",
            "ad_category": "tool_app", "creative_type": "text",
            "content": {
                "title": "专业手机清理工具",
                "description": "深度清理垃圾，提升手机流畅度",
                "cta_text": "免费下载"
            }
        }
    },

    # ═══ 模块G：综合场景（6条）═══

    {
        "case_id": "T-G01", "desc": "【综合】全维度违规-多重blocking",
        "expected": "returned",
        "verify_dims": ["text_violation", "image_safety", "landing_page",
                        "qualification", "platform_rule", "consistency"],
        "data": {
            "request_id": "T-G01", "advertiser_id": "adv-game-007",
            "ad_category": "game", "creative_type": "mixed",
            "content": {
                "title": "全球最强手游，充值返利月入万元",
                "description": "充值1000返3000，全服第一，保证收益",
                "cta_text": "立即充值",
                "image_urls": ["https://www.baidu.com/img/PCtm_d9c8750bed0b3c7d089fa7d55720d6cf.png"],
                "landing_page_url": "https://www.apple.com/app-store/"
            }
        }
    },
    {
        "case_id": "T-G02", "desc": "【综合】合规全通过-全维度",
        "expected": "pass", "verify_dims": ["text_violation", "image_safety", "landing_page"],
        "data": {
            "request_id": "T-G02", "advertiser_id": "adv-edu-005",
            "ad_category": "education", "creative_type": "mixed",
            "content": {
                "title": "W3Schools学习平台",
                "description": "免费学习Web技术，随时随地练习",
                "cta_text": "开始学习",
                "image_urls": ["https://www.w3schools.com/css/img_5terre.jpg"],
                "landing_page_url": "https://www.w3schools.com"
            }
        }
    },
    {
        "case_id": "T-G03", "desc": "【综合】降级不拉分验证",
        "expected": "returned", "verify_dims": [],
        "data": {
            "request_id": "T-G03", "advertiser_id": "adv-game-008",
            "ad_category": "game", "creative_type": "text",
            "content": {
                "title": "全球最强游戏，充值返利月入万元",
                "description": "充值1000返3000，月入万元",
                "cta_text": "立即充值",
                "landing_page_url": "https://this-domain-404.com/page"
            }
        }
    },
    {
        "case_id": "T-G04", "desc": "【综合】边界多项叠加",
        "expected": "review", "verify_dims": [],
        "data": {
            "request_id": "T-G04", "advertiser_id": "adv-ecom-006",
            "ad_category": "ecommerce", "creative_type": "mixed",
            "content": {
                "title": "超值好物，效果显著深受好评",
                "description": "用户反馈良好，业界认可，长期增值",
                "cta_text": "查看详情",
                "image_urls": ["https://www.w3schools.com/css/img_5terre.jpg"]
            }
        }
    },
    {
        "case_id": "T-G05", "desc": "【综合】资质+文案双违规",
        "expected": "returned", "verify_dims": ["qualification"],
        "data": {
            "request_id": "T-G05", "advertiser_id": "adv-fin-003",
            "ad_category": "finance", "creative_type": "text",
            "content": {
                "title": "专业理财，年化8%稳健收益",
                "description": "历史业绩优良，长期稳健，投资有风险",
                "cta_text": "了解更多"
            }
        }
    },
    {
        "case_id": "T-G06", "desc": "【综合】修改后重提-合规",
        "expected": "pass", "verify_dims": [],
        "data": {
            "request_id": "T-G06", "advertiser_id": "adv-tool-015",
            "ad_category": "tool_app", "creative_type": "text",
            "content": {
                "title": "手机清理工具",
                "description": "释放存储空间，提升运行速度",
                "cta_text": "免费下载"
            }
        }
    },

    # ═══ 模块I：文案边界深化（10条）═══

    {
        "case_id": "T-I01", "desc": "【金融-边界】历史业绩宣传（非明确承诺）",
        "expected": "review", "verify_dims": [],
        "data": {
            "request_id": "T-I01", "advertiser_id": "adv-fin-010",
            "ad_category": "finance", "creative_type": "text",
            "content": {
                "title": "专业资产管理，历史年化回报稳健",
                "description": "10年深耕价值投资，历史业绩优良，长期陪伴财富成长",
                "cta_text": "了解更多"
            },
            "advertiser_qualification_ids": ["LIC-CBIRC-20240001"]
        }
    },
    {
        "case_id": "T-I02", "desc": "【金融-违规】隐性收益承诺",
        "expected": "returned", "verify_dims": [],
        "data": {
            "request_id": "T-I02", "advertiser_id": "adv-fin-011",
            "ad_category": "finance", "creative_type": "text",
            "content": {
                "title": "每月稳定入账，轻松实现财务自由",
                "description": "签约锁定收益，无论市场涨跌，月月准时到账",
                "cta_text": "立即签约"
            }
        }
    },
    {
        "case_id": "T-I03", "desc": "【医疗-边界】功效描述模糊",
        "expected": "review", "verify_dims": [],
        "data": {
            "request_id": "T-I03", "advertiser_id": "adv-health-010",
            "ad_category": "health", "creative_type": "text",
            "content": {
                "title": "天然植物精华，改善睡眠质量",
                "description": "帮助放松身心，有助于提升睡眠体验，温和无依赖",
                "cta_text": "了解详情"
            }
        }
    },
    {
        "case_id": "T-I04", "desc": "【医疗-违规】以科学之名虚假宣传",
        "expected": "returned", "verify_dims": [],
        "data": {
            "request_id": "T-I04", "advertiser_id": "adv-health-011",
            "ad_category": "health", "creative_type": "text",
            "content": {
                "title": "诺贝尔奖技术加持，细胞级修复受损器官",
                "description": "权威机构认证，临床数据证实，有效率高达99.7%",
                "cta_text": "立即购买"
            }
        }
    },
    {
        "case_id": "T-I05", "desc": "【教育-边界】就业前景描述",
        "expected": "review", "verify_dims": [],
        "data": {
            "request_id": "T-I05", "advertiser_id": "adv-edu-010",
            "ad_category": "education", "creative_type": "text",
            "content": {
                "title": "学完Python，轻松找到高薪工作",
                "description": "学员平均薪资涨幅明显，众多企业认可的技能培训",
                "cta_text": "免费试听"
            }
        }
    },
    {
        "case_id": "T-I06", "desc": "【教育-违规】虚假就业数据",
        "expected": "returned", "verify_dims": [],
        "data": {
            "request_id": "T-I06", "advertiser_id": "adv-edu-011",
            "ad_category": "education", "creative_type": "text",
            "content": {
                "title": "就业率100%，平均薪资3万，签约大厂",
                "description": "已帮助5000名学员入职BAT，不就业全额退款",
                "cta_text": "立即报名"
            }
        }
    },
    {
        "case_id": "T-I07", "desc": "【电商-边界】夸张但模糊的效果",
        "expected": "review", "verify_dims": [],
        "data": {
            "request_id": "T-I07", "advertiser_id": "adv-ecom-010",
            "ad_category": "ecommerce", "creative_type": "text",
            "content": {
                "title": "神奇面膜，28天焕新肌肤",
                "description": "深层修护，坚持使用后皮肤状态明显改善",
                "cta_text": "立即购买"
            }
        }
    },
    {
        "case_id": "T-I08", "desc": "【电商-违规】虚假原价+极限词组合",
        "expected": "returned", "verify_dims": [],
        "data": {
            "request_id": "T-I08", "advertiser_id": "adv-ecom-011",
            "ad_category": "ecommerce", "creative_type": "text",
            "content": {
                "title": "全球销量第一护肤品，原价999今日仅售9.9",
                "description": "好评率100%，唯一获得国际最高奖项认证",
                "cta_text": "立即抢购"
            }
        }
    },
    {
        "case_id": "T-I09", "desc": "【工具-边界】功能夸大但未明确违规",
        "expected": "review", "verify_dims": [],
        "data": {
            "request_id": "T-I09", "advertiser_id": "adv-tool-020",
            "ad_category": "tool_app", "creative_type": "text",
            "content": {
                "title": "AI超级清理，性能提升200%",
                "description": "智能算法深度优化，让老手机焕发新生",
                "cta_text": "免费下载"
            }
        }
    },
    {
        "case_id": "T-I10", "desc": "【对抗-高级变体】多种混合绕过手段",
        "expected": "returned", "verify_dims": [],
        "data": {
            "request_id": "T-I10", "advertiser_id": "adv-tool-021",
            "ad_category": "tool_app", "creative_type": "text",
            "content": {
                "title": "全*球*最强清理，Di\u2460名体验，b\u01ceo证有效",
                "description": "销量超.过.1.亿，好评率9\ufe0f\u20e39\ufe0f\u20e3%，zui好的选择",
                "cta_text": "下载"
            }
        }
    },

    # ═══ 模块J：资质深化（8条）═══

    {
        "case_id": "T-J01", "desc": "【游戏-违规】版号格式错误",
        "expected": "returned", "verify_dims": ["qualification"],
        "data": {
            "request_id": "T-J01", "advertiser_id": "adv-game-010",
            "ad_category": "game", "creative_type": "text",
            "content": {
                "title": "全新RPG手游，开启冒险旅程",
                "description": "精美画面，丰富剧情，百万玩家已加入",
                "cta_text": "立即下载"
            },
            "advertiser_qualification_ids": ["INVALID-FORMAT-123"]
        }
    },
    {
        "case_id": "T-J02", "desc": "【游戏-合规】正确版号格式",
        "expected": "pass", "verify_dims": ["qualification"],
        "data": {
            "request_id": "T-J02", "advertiser_id": "adv-game-011",
            "ad_category": "game", "creative_type": "text",
            "content": {
                "title": "休闲益智小游戏",
                "description": "简单有趣，适合全年龄段，随时随地畅玩",
                "cta_text": "免费下载"
            },
            "advertiser_qualification_ids": ["ISBN978-7-200-11111"]
        }
    },
    {
        "case_id": "T-J03", "desc": "【金融-合规】有牌照+合规文案",
        "expected": "pass", "verify_dims": ["qualification"],
        "data": {
            "request_id": "T-J03", "advertiser_id": "adv-fin-012",
            "ad_category": "finance", "creative_type": "text",
            "content": {
                "title": "专业理财规划服务",
                "description": "多元化配置，专业顾问服务。投资有风险，入市需谨慎。",
                "cta_text": "免费咨询"
            },
            "advertiser_qualification_ids": ["LIC-CBIRC-20240001"]
        }
    },
    {
        "case_id": "T-J04", "desc": "【金融-违规】有牌照但文案违规",
        "expected": "returned", "verify_dims": ["qualification"],
        "data": {
            "request_id": "T-J04", "advertiser_id": "adv-fin-013",
            "ad_category": "finance", "creative_type": "text",
            "content": {
                "title": "持牌机构保证收益，稳赚不赔理财",
                "description": "正规持牌，本金保障，年化8%固定回报",
                "cta_text": "立即投资"
            },
            "advertiser_qualification_ids": ["LIC-CBIRC-20240001"]
        }
    },
    {
        "case_id": "T-J05", "desc": "【医疗-合规】有批准文号+合规文案",
        "expected": "pass", "verify_dims": ["qualification"],
        "data": {
            "request_id": "T-J05", "advertiser_id": "adv-health-012",
            "ad_category": "health", "creative_type": "text",
            "content": {
                "title": "XX保健食品，辅助改善睡眠",
                "description": "经国家认证，适合睡眠质量欠佳的成年人，本品不能替代药物治疗",
                "cta_text": "了解详情"
            },
            "advertiser_qualification_ids": ["MED-APPROVAL-2024001"]
        }
    },
    {
        "case_id": "T-J06", "desc": "【教育-合规】非学科类培训无需资质",
        "expected": "pass", "verify_dims": [],
        "data": {
            "request_id": "T-J06", "advertiser_id": "adv-edu-012",
            "ad_category": "education", "creative_type": "text",
            "content": {
                "title": "摄影技能提升课程",
                "description": "专业摄影师授课，从入门到进阶，掌握构图用光技巧",
                "cta_text": "免费试听"
            }
        }
    },
    {
        "case_id": "T-J07", "desc": "【游戏-违规】无版号+违规文案双重违规",
        "expected": "returned", "verify_dims": ["qualification"],
        "data": {
            "request_id": "T-J07", "advertiser_id": "adv-game-012",
            "ad_category": "game", "creative_type": "text",
            "content": {
                "title": "最强策略游戏，充值必赚，月入过万",
                "description": "全服第一体验，充值1000返5000",
                "cta_text": "立即充值"
            }
        }
    },
    {
        "case_id": "T-J08", "desc": "【医疗-违规】无资质+疗效承诺",
        "expected": "returned", "verify_dims": ["qualification"],
        "data": {
            "request_id": "T-J08", "advertiser_id": "adv-health-013",
            "ad_category": "health", "creative_type": "text",
            "content": {
                "title": "祖传秘方，专治颈椎病腰椎病",
                "description": "三代传承，百年配方，治愈率95%，永不复发",
                "cta_text": "立即购买"
            }
        }
    },

    # ═══ 模块K：落地页深化（8条）═══

    {
        "case_id": "T-K01", "desc": "【落地页-合规】产品与落地页高度一致",
        "expected": "pass", "verify_dims": ["landing_page"],
        "data": {
            "request_id": "T-K01", "advertiser_id": "adv-edu-020",
            "ad_category": "education", "creative_type": "text",
            "content": {
                "title": "W3Schools Python教程",
                "description": "从零学Python，免费在线练习，随时检验学习成果",
                "cta_text": "开始学习",
                "landing_page_url": "https://www.w3schools.com/python/"
            }
        }
    },
    {
        "case_id": "T-K02", "desc": "【落地页-违规】CTA与落地页行动不符",
        "expected": "returned", "verify_dims": ["landing_page"],
        "data": {
            "request_id": "T-K02", "advertiser_id": "adv-tool-022",
            "ad_category": "tool_app", "creative_type": "text",
            "content": {
                "title": "立即免费下载，一键安装",
                "description": "点击即可免费获取，无需注册",
                "cta_text": "免费下载",
                "landing_page_url": "https://www.apple.com/app-store/"
            }
        }
    },
    {
        "case_id": "T-K03", "desc": "【落地页-边界】落地页信息比广告丰富",
        "expected": "pass", "verify_dims": ["landing_page"],
        "data": {
            "request_id": "T-K03", "advertiser_id": "adv-tool-023",
            "ad_category": "tool_app", "creative_type": "text",
            "content": {
                "title": "百度地图导航",
                "description": "实时路况，智能规划路线",
                "cta_text": "立即使用",
                "landing_page_url": "https://www.baidu.com"
            }
        }
    },
    {
        "case_id": "T-K04", "desc": "【落地页-违规】品牌完全不符",
        "expected": "returned", "verify_dims": ["landing_page"],
        "data": {
            "request_id": "T-K04", "advertiser_id": "adv-tool-024",
            "ad_category": "tool_app", "creative_type": "text",
            "content": {
                "title": "vivo官方应用商店精选推荐",
                "description": "vivo官方精选优质应用，安全可靠",
                "cta_text": "立即下载",
                "landing_page_url": "https://www.apple.com/app-store/"
            }
        }
    },
    {
        "case_id": "T-K05", "desc": "【落地页-边界】落地页语言与广告不同",
        "expected": "pass", "verify_dims": ["landing_page"],
        "data": {
            "request_id": "T-K05", "advertiser_id": "adv-edu-021",
            "ad_category": "education", "creative_type": "text",
            "content": {
                "title": "国际领先的编程学习平台",
                "description": "全球开发者首选，多语言教程",
                "cta_text": "开始学习",
                "landing_page_url": "https://www.w3schools.com"
            }
        }
    },
    {
        "case_id": "T-K06", "desc": "【落地页-合规】金融广告落地页一致",
        "expected": "pass", "verify_dims": ["landing_page"],
        "data": {
            "request_id": "T-K06", "advertiser_id": "adv-fin-020",
            "ad_category": "finance", "creative_type": "text",
            "content": {
                "title": "专业股票分析工具",
                "description": "实时行情，智能选股，投资有风险入市需谨慎",
                "cta_text": "了解更多",
                "landing_page_url": "https://www.baidu.com"
            },
            "advertiser_qualification_ids": ["LIC-CSRC-20240001"]
        }
    },
    {
        "case_id": "T-K07", "desc": "【落地页-违规】游戏广告落地页完全不相关",
        "expected": "review", "verify_dims": ["landing_page"],
        "data": {
            "request_id": "T-K07", "advertiser_id": "adv-game-020",
            "ad_category": "game", "creative_type": "text",
            "content": {
                "title": "三国策略手游，万人同服",
                "description": "史诗战争，与好友并肩作战",
                "cta_text": "立即下载",
                "landing_page_url": "https://www.apple.com"
            },
            "advertiser_qualification_ids": ["ISBN978-7-100-00001"]
        }
    },
    {
        "case_id": "T-K08", "desc": "【落地页-降级】落地页超时（验证降级不拉分）",
        "expected": "pass", "verify_dims": [],
        "data": {
            "request_id": "T-K08", "advertiser_id": "adv-tool-025",
            "ad_category": "tool_app", "creative_type": "text",
            "content": {
                "title": "手机清理工具专业版",
                "description": "深度清理，释放空间，提升性能",
                "cta_text": "免费下载",
                "landing_page_url": "https://timeout.example.invalid/"
            }
        }
    },

    # ═══ 模块L：图片深化（8条）═══

    {
        "case_id": "T-L01", "desc": "【图片-合规】风景图+旅游类广告",
        "expected": "pass", "verify_dims": ["image_safety"],
        "data": {
            "request_id": "T-L01", "advertiser_id": "adv-ecom-020",
            "ad_category": "ecommerce", "creative_type": "mixed",
            "content": {
                "title": "携程旅行，发现美好世界",
                "description": "全球酒店机票预订，出行首选平台",
                "cta_text": "立即预订",
                "image_urls": ["https://www.w3schools.com/css/img_5terre.jpg"]
            }
        }
    },
    {
        "case_id": "T-L02", "desc": "【图片-合规】产品使用场景图+功能文案",
        "expected": "pass", "verify_dims": ["image_safety"],
        "data": {
            "request_id": "T-L02", "advertiser_id": "adv-edu-022",
            "ad_category": "education", "creative_type": "mixed",
            "content": {
                "title": "手机摄影技巧课程",
                "description": "学会用手机拍出专业级照片",
                "cta_text": "免费试学",
                "image_urls": ["https://www.w3schools.com/css/img_5terre.jpg"]
            }
        }
    },
    {
        "case_id": "T-L03", "desc": "【图片-边界】科技产品配风景图",
        "expected": "review", "verify_dims": ["consistency"],
        "data": {
            "request_id": "T-L03", "advertiser_id": "adv-tool-026",
            "ad_category": "tool_app", "creative_type": "mixed",
            "content": {
                "title": "AI智能手机助手App",
                "description": "语音控制，智能管理，科技生活新体验",
                "cta_text": "免费下载",
                "image_urls": ["https://www.w3schools.com/css/img_5terre.jpg"]
            }
        }
    },
    {
        "case_id": "T-L04", "desc": "【图片-合规】多图各自合规",
        "expected": "pass", "verify_dims": ["image_safety"],
        "data": {
            "request_id": "T-L04", "advertiser_id": "adv-ecom-021",
            "ad_category": "ecommerce", "creative_type": "mixed",
            "content": {
                "title": "精品旅游路线推荐",
                "description": "专业导游带队，深度体验当地文化",
                "cta_text": "立即预订",
                "image_urls": [
                    "https://www.w3schools.com/css/img_5terre.jpg",
                    "https://www.w3schools.com/css/img_5terre.jpg"
                ]
            }
        }
    },
    {
        "case_id": "T-L05", "desc": "【图片-合规】合规文案+合规图+有版号游戏",
        "expected": "pass", "verify_dims": ["image_safety", "qualification"],
        "data": {
            "request_id": "T-L05", "advertiser_id": "adv-game-021",
            "ad_category": "game", "creative_type": "mixed",
            "content": {
                "title": "海岛冒险手游",
                "description": "探索神秘海岛，解锁冒险剧情",
                "cta_text": "立即下载",
                "image_urls": ["https://www.w3schools.com/css/img_5terre.jpg"]
            },
            "advertiser_qualification_ids": ["ISBN978-7-100-55555"]
        }
    },
    {
        "case_id": "T-L06", "desc": "【图片-违规】违规文案+任意图片(验证全维度)",
        "expected": "returned", "verify_dims": ["text_violation", "image_safety"],
        "data": {
            "request_id": "T-L06", "advertiser_id": "adv-fin-021",
            "ad_category": "finance", "creative_type": "mixed",
            "content": {
                "title": "保本保息理财，年化收益12%",
                "description": "零风险投资，签约即锁定收益",
                "cta_text": "立即投资",
                "image_urls": ["https://www.w3schools.com/css/img_5terre.jpg"]
            }
        }
    },
    {
        "case_id": "T-L07", "desc": "【图片-合规】品牌图片与品牌文案完全一致",
        "expected": "pass", "verify_dims": ["image_safety", "consistency"],
        "data": {
            "request_id": "T-L07", "advertiser_id": "adv-tool-027",
            "ad_category": "tool_app", "creative_type": "mixed",
            "content": {
                "title": "百度文库，海量文档资源",
                "description": "学习资料、工作文档一站获取",
                "cta_text": "立即使用",
                "image_urls": ["https://www.baidu.com/img/PCtm_d9c8750bed0b3c7d089fa7d55720d6cf.png"]
            }
        }
    },
    {
        "case_id": "T-L08", "desc": "【图片-合规】抽象图案无明显品牌",
        "expected": "pass", "verify_dims": ["image_safety"],
        "data": {
            "request_id": "T-L08", "advertiser_id": "adv-tool-028",
            "ad_category": "tool_app", "creative_type": "mixed",
            "content": {
                "title": "创意设计工具",
                "description": "专业设计软件，让创意无限延伸",
                "cta_text": "免费试用",
                "image_urls": ["https://www.w3schools.com/css/img_5terre.jpg"]
            }
        }
    },

    # ═══ 模块M：视频深化（6条，异步）═══

    {
        "case_id": "T-M01", "desc": "【视频-合规】合规文案+合规视频+有版号",
        "expected": "pass", "verify_dims": ["image_safety", "qualification"],
        "is_async": True,
        "data": {
            "request_id": "T-M01", "advertiser_id": "adv-game-030",
            "ad_category": "game", "creative_type": "video",
            "content": {
                "title": "休闲消除手游",
                "description": "轻松益智，全年龄适合，随时畅玩",
                "cta_text": "免费下载",
                "video_url": "https://www.w3schools.com/html/mov_bbb.mp4"
            },
            "advertiser_qualification_ids": ["ISBN978-7-100-88888"]
        }
    },
    {
        "case_id": "T-M02", "desc": "【视频-违规】金融违规文案+视频",
        "expected": "returned", "verify_dims": ["text_violation", "image_safety"],
        "is_async": True,
        "data": {
            "request_id": "T-M02", "advertiser_id": "adv-fin-030",
            "ad_category": "finance", "creative_type": "video",
            "content": {
                "title": "保本保息理财视频介绍，年化8%稳定收益",
                "description": "零风险，签约即锁定，月月到账",
                "cta_text": "立即投资",
                "video_url": "https://www.w3schools.com/html/mov_bbb.mp4"
            }
        }
    },
    {
        "case_id": "T-M03", "desc": "【视频+图片-违规】多素材同时有违规文案",
        "expected": "returned", "verify_dims": ["text_violation", "image_safety"],
        "is_async": True,
        "data": {
            "request_id": "T-M03", "advertiser_id": "adv-game-031",
            "ad_category": "game", "creative_type": "mixed",
            "content": {
                "title": "最好的游戏，充值第一，月入万元",
                "description": "全球唯一采用AI技术的手游",
                "cta_text": "立即充值",
                "image_urls": ["https://www.w3schools.com/css/img_5terre.jpg"],
                "video_url": "https://www.w3schools.com/html/mov_bbb.mp4"
            },
            "advertiser_qualification_ids": ["ISBN978-7-100-00001"]
        }
    },
    {
        "case_id": "T-M04", "desc": "【视频+落地页-合规】全要素一致",
        "expected": "pass", "verify_dims": ["image_safety", "landing_page"],
        "is_async": True,
        "data": {
            "request_id": "T-M04", "advertiser_id": "adv-edu-030",
            "ad_category": "education", "creative_type": "video",
            "content": {
                "title": "W3Schools JavaScript教程",
                "description": "从基础到高级，免费在线学习",
                "cta_text": "开始学习",
                "video_url": "https://www.w3schools.com/html/mov_bbb.mp4",
                "landing_page_url": "https://www.w3schools.com/js/"
            }
        }
    },
    {
        "case_id": "T-M05", "desc": "【视频-合规】工具类正常视频广告",
        "expected": "pass", "verify_dims": ["image_safety"],
        "is_async": True,
        "data": {
            "request_id": "T-M05", "advertiser_id": "adv-tool-030",
            "ad_category": "tool_app", "creative_type": "video",
            "content": {
                "title": "文件压缩工具使用教程",
                "description": "一键压缩，节省存储空间，支持所有格式",
                "cta_text": "免费下载",
                "video_url": "https://www.w3schools.com/html/mov_bbb.mp4"
            }
        }
    },
    {
        "case_id": "T-M06", "desc": "【视频+图片+落地页-全维度】最复杂场景",
        "expected": "pass", "verify_dims": ["image_safety", "landing_page", "consistency"],
        "is_async": True,
        "data": {
            "request_id": "T-M06", "advertiser_id": "adv-tool-031",
            "ad_category": "tool_app", "creative_type": "mixed",
            "content": {
                "title": "百度网盘云存储服务",
                "description": "海量存储，随时访问，安全备份",
                "cta_text": "立即使用",
                "image_urls": ["https://www.baidu.com/img/PCtm_d9c8750bed0b3c7d089fa7d55720d6cf.png"],
                "video_url": "https://www.w3schools.com/html/mov_bbb.mp4",
                "landing_page_url": "https://www.baidu.com"
            }
        }
    },

    # ═══ 模块N：平台规范深化（6条）═══

    {
        "case_id": "T-N01", "desc": "【平台-合规】适配vivo的正常表述",
        "expected": "pass", "verify_dims": ["platform_rule"],
        "data": {
            "request_id": "T-N01", "advertiser_id": "adv-tool-040",
            "ad_category": "tool_app", "creative_type": "text",
            "content": {
                "title": "专为vivo手机优化的清理工具",
                "description": "完美适配vivo系统，深度清理更彻底",
                "cta_text": "免费下载"
            }
        }
    },
    {
        "case_id": "T-N02", "desc": "【平台-违规】使用vivo官方字样",
        "expected": "returned", "verify_dims": ["platform_rule"],
        "data": {
            "request_id": "T-N02", "advertiser_id": "adv-tool-041",
            "ad_category": "tool_app", "creative_type": "text",
            "content": {
                "title": "vivo官方合作，独家授权清理工具",
                "description": "经vivo官方认证，品质保障",
                "cta_text": "立即下载"
            }
        }
    },
    {
        "case_id": "T-N03", "desc": "【平台-违规】直接攻击竞品",
        "expected": "returned", "verify_dims": ["platform_rule"],
        "data": {
            "request_id": "T-N03", "advertiser_id": "adv-tool-042",
            "ad_category": "tool_app", "creative_type": "text",
            "content": {
                "title": "秒杀华为手机管家，远超小米清理大师",
                "description": "同类测试第一，OPPO用户纷纷转投",
                "cta_text": "立即下载"
            }
        }
    },
    {
        "case_id": "T-N04", "desc": "【平台-边界】暗示行业领先但不点名竞品",
        "expected": "review", "verify_dims": [],
        "data": {
            "request_id": "T-N04", "advertiser_id": "adv-tool-043",
            "ad_category": "tool_app", "creative_type": "text",
            "content": {
                "title": "新一代清理技术，行业标杆",
                "description": "超越传统清理方式，引领技术革新",
                "cta_text": "立即体验"
            }
        }
    },
    {
        "case_id": "T-N05", "desc": "【平台-合规】客观描述自身优势",
        "expected": "pass", "verify_dims": [],
        "data": {
            "request_id": "T-N05", "advertiser_id": "adv-tool-044",
            "ad_category": "tool_app", "creative_type": "text",
            "content": {
                "title": "深度清理技术，三步释放空间",
                "description": "独家算法，精准识别垃圾文件，清理效率提升显著",
                "cta_text": "免费下载"
            }
        }
    },
    {
        "case_id": "T-N06", "desc": "【平台-违规】低俗性暗示+绝对化",
        "expected": "returned", "verify_dims": [],
        "data": {
            "request_id": "T-N06", "advertiser_id": "adv-health-020",
            "ad_category": "health", "creative_type": "text",
            "content": {
                "title": "全球最强男性保健品，今夜让她满意",
                "description": "持久战斗力秘方，效果第一，保证满足",
                "cta_text": "立即购买"
            }
        }
    },

    # ═══ 模块O：一致性深化（8条）═══

    {
        "case_id": "T-O01", "desc": "【一致性-合规】氛围图+功能文案",
        "expected": "pass", "verify_dims": ["consistency"],
        "data": {
            "request_id": "T-O01", "advertiser_id": "adv-tool-050",
            "ad_category": "tool_app", "creative_type": "mixed",
            "content": {
                "title": "旅行记录App，记录每次美好旅程",
                "description": "拍照打卡，地图轨迹，旅行日记一站搞定",
                "cta_text": "免费下载",
                "image_urls": ["https://www.w3schools.com/css/img_5terre.jpg"]
            }
        }
    },
    {
        "case_id": "T-O02", "desc": "【一致性-违规】品牌明确冲突",
        "expected": "returned", "verify_dims": ["consistency"],
        "data": {
            "request_id": "T-O02", "advertiser_id": "adv-tool-051",
            "ad_category": "tool_app", "creative_type": "mixed",
            "content": {
                "title": "腾讯微信官方版",
                "description": "最新版微信，功能更强大",
                "cta_text": "立即下载",
                "image_urls": ["https://www.baidu.com/img/PCtm_d9c8750bed0b3c7d089fa7d55720d6cf.png"]
            }
        }
    },
    {
        "case_id": "T-O03", "desc": "【一致性-合规】产品截图+产品文案",
        "expected": "pass", "verify_dims": ["consistency"],
        "data": {
            "request_id": "T-O03", "advertiser_id": "adv-tool-052",
            "ad_category": "tool_app", "creative_type": "mixed",
            "content": {
                "title": "百度App最新版",
                "description": "搜索更智能，体验更流畅",
                "cta_text": "立即下载",
                "image_urls": ["https://www.baidu.com/img/PCtm_d9c8750bed0b3c7d089fa7d55720d6cf.png"]
            }
        }
    },
    {
        "case_id": "T-O04", "desc": "【一致性-边界】产品类别错位",
        "expected": "review", "verify_dims": ["consistency"],
        "data": {
            "request_id": "T-O04", "advertiser_id": "adv-ecom-030",
            "ad_category": "ecommerce", "creative_type": "mixed",
            "content": {
                "title": "有机蔬菜配送，新鲜直达",
                "description": "每日精选有机蔬菜，24小时配送到家",
                "cta_text": "立即下单",
                "image_urls": ["https://www.baidu.com/img/PCtm_d9c8750bed0b3c7d089fa7d55720d6cf.png"]
            }
        }
    },
    {
        "case_id": "T-O05", "desc": "【一致性-合规】落地页与广告高度一致",
        "expected": "pass", "verify_dims": ["landing_page", "consistency"],
        "data": {
            "request_id": "T-O05", "advertiser_id": "adv-edu-040",
            "ad_category": "education", "creative_type": "text",
            "content": {
                "title": "Python编程零基础入门",
                "description": "系统学习Python，从变量到项目实战",
                "cta_text": "开始学习",
                "landing_page_url": "https://www.w3schools.com/python/"
            }
        }
    },
    {
        "case_id": "T-O06", "desc": "【一致性-边界】落地页语言完全不同产品",
        "expected": "review", "verify_dims": ["landing_page"],
        "data": {
            "request_id": "T-O06", "advertiser_id": "adv-tool-053",
            "ad_category": "tool_app", "creative_type": "text",
            "content": {
                "title": "国产手机清理神器",
                "description": "专为中国用户设计，一键极速清理",
                "cta_text": "免费下载",
                "landing_page_url": "https://www.apple.com"
            }
        }
    },
    {
        "case_id": "T-O07", "desc": "【一致性-合规】多图合规+落地页一致",
        "expected": "pass", "verify_dims": ["image_safety", "landing_page", "consistency"],
        "data": {
            "request_id": "T-O07", "advertiser_id": "adv-edu-041",
            "ad_category": "education", "creative_type": "mixed",
            "content": {
                "title": "W3Schools全栈开发课程",
                "description": "HTML/CSS/JS/Python一站学习",
                "cta_text": "开始学习",
                "image_urls": ["https://www.w3schools.com/css/img_5terre.jpg"],
                "landing_page_url": "https://www.w3schools.com"
            }
        }
    },
    {
        "case_id": "T-O08", "desc": "【一致性-违规】文案+图片+落地页三方不一致",
        "expected": "returned", "verify_dims": ["consistency"],
        "data": {
            "request_id": "T-O08", "advertiser_id": "adv-game-040",
            "ad_category": "game", "creative_type": "mixed",
            "content": {
                "title": "vivo游戏中心独家游戏",
                "description": "vivo平台专属，精品游戏首发",
                "cta_text": "立即下载",
                "image_urls": ["https://www.baidu.com/img/PCtm_d9c8750bed0b3c7d089fa7d55720d6cf.png"],
                "landing_page_url": "https://www.apple.com/app-store/"
            },
            "advertiser_qualification_ids": ["ISBN978-7-100-00001"]
        }
    },

    # ═══ 模块P：综合场景深化（6条）═══

    {
        "case_id": "T-P01", "desc": "【综合-合规】最复杂的全维度通过",
        "expected": "pass", "verify_dims": ["text_violation", "image_safety", "landing_page", "consistency"],
        "data": {
            "request_id": "T-P01", "advertiser_id": "adv-tool-060",
            "ad_category": "tool_app", "creative_type": "mixed",
            "content": {
                "title": "百度地图专业导航",
                "description": "实时路况，智能规划，语音播报，安全出行首选",
                "cta_text": "立即使用",
                "image_urls": ["https://www.baidu.com/img/PCtm_d9c8750bed0b3c7d089fa7d55720d6cf.png"],
                "landing_page_url": "https://www.baidu.com"
            }
        }
    },
    {
        "case_id": "T-P02", "desc": "【综合-违规】五维度同时触发（最严重场景）",
        "expected": "returned", "verify_dims": ["text_violation", "image_safety", "qualification"],
        "data": {
            "request_id": "T-P02", "advertiser_id": "adv-health-030",
            "ad_category": "health", "creative_type": "mixed",
            "content": {
                "title": "全球最强医疗神药，根治糖尿病保证有效",
                "description": "诺贝尔奖认证，有效率100%，永不复发，替代胰岛素，月入万元代理",
                "cta_text": "立即购买",
                "image_urls": ["https://www.baidu.com/img/PCtm_d9c8750bed0b3c7d089fa7d55720d6cf.png"],
                "landing_page_url": "https://www.apple.com/app-store/"
            }
        }
    },
    {
        "case_id": "T-P03", "desc": "【综合-边界】所有维度都是边界案例",
        "expected": "review", "verify_dims": [],
        "data": {
            "request_id": "T-P03", "advertiser_id": "adv-health-031",
            "ad_category": "health", "creative_type": "mixed",
            "content": {
                "title": "效果显著的健康管理App，用户好评如潮",
                "description": "历史数据优良，长期稳健提升，深受用户认可",
                "cta_text": "了解详情",
                "image_urls": ["https://www.w3schools.com/css/img_5terre.jpg"],
                "landing_page_url": "https://www.w3schools.com"
            }
        }
    },
    {
        "case_id": "T-P04", "desc": "【综合-违规】对抗性变体+无资质+违规落地页",
        "expected": "returned", "verify_dims": ["qualification"],
        "data": {
            "request_id": "T-P04", "advertiser_id": "adv-fin-040",
            "ad_category": "finance", "creative_type": "text",
            "content": {
                "title": "zui好理财产品，b\u01ceo证收益，Di\u2460名机构",
                "description": "年化8%固.定.回.报，零\u2605风\u2605险投资",
                "cta_text": "立即投资",
                "landing_page_url": "https://www.apple.com/app-store/"
            }
        }
    },
    {
        "case_id": "T-P05", "desc": "【综合-合规】游戏全维度合规最复杂",
        "expected": "pass",
        "verify_dims": ["image_safety", "landing_page", "qualification", "consistency"],
        "is_async": True,
        "data": {
            "request_id": "T-P05", "advertiser_id": "adv-game-050",
            "ad_category": "game", "creative_type": "mixed",
            "content": {
                "title": "海岛冒险策略手游",
                "description": "探索神秘岛屿，建立自己的帝国，与全球玩家竞技，适龄提示：12+",
                "cta_text": "立即下载",
                "image_urls": ["https://www.w3schools.com/css/img_5terre.jpg"],
                "video_url": "https://www.w3schools.com/html/mov_bbb.mp4",
                "landing_page_url": "https://www.w3schools.com"
            },
            "advertiser_qualification_ids": ["ISBN978-7-100-77777"]
        }
    },
    {
        "case_id": "T-P06", "desc": "【综合-违规】品牌冒用+竞品攻击+绝对化",
        "expected": "returned", "verify_dims": ["platform_rule"],
        "data": {
            "request_id": "T-P06", "advertiser_id": "adv-tool-061",
            "ad_category": "tool_app", "creative_type": "text",
            "content": {
                "title": "vivo官方认证，比华为更好用的第一清理工具",
                "description": "唯一经过vivo官方授权，完爆所有竞品",
                "cta_text": "立即下载"
            }
        }
    },
]


# ============================================================
# Verdict 匹配逻辑
# ============================================================

def verdict_match(expected: str, actual: str) -> bool:
    """判断 verdict 是否匹配。"""
    if expected == actual:
        return True
    # returned ↔ reject 互等价
    if {expected, actual} == {"reject", "returned"}:
        return True
    # review 可接受 returned（更保守）
    if expected == "review" and actual in ("review", "returned"):
        return True
    return False


# ============================================================
# 提交 & 轮询
# ============================================================

async def submit_case(
    client: httpx.AsyncClient, case: dict, semaphore: asyncio.Semaphore,
) -> dict:
    """提交单个测试用例。"""
    async with semaphore:
        start = time.time()
        try:
            resp = await client.post(
                f"{BASE_URL}/review", json=case["data"],
                headers={"X-API-Key": API_KEY}, timeout=120.0,
            )
            result = resp.json()
            duration = round((time.time() - start) * 1000)

            if result.get("mode") == "async":
                task_id = result.get("task_id")
                result = await poll_task(client, task_id, timeout=120)
                duration = round((time.time() - start) * 1000)

            actual = result.get("verdict", "error")
            expected = case["expected"]
            passed = verdict_match(expected, actual)
            checked = result.get("checked_dimensions", [])

            # 验证 verify_dims
            verify_ok = True
            verify_dims = case.get("verify_dims", [])
            for dim in verify_dims:
                if dim not in checked:
                    verify_ok = False

            return {
                "case_id": case["case_id"], "desc": case["desc"],
                "expected": expected, "actual": actual,
                "confidence": result.get("confidence", 0),
                "violations": len(result.get("violations", [])),
                "checked_dimensions": checked,
                "duration_ms": duration, "passed": passed,
                "verify_ok": verify_ok, "verify_dims": verify_dims,
                "is_fallback": result.get("is_fallback", False),
            }
        except Exception as e:
            return {
                "case_id": case["case_id"], "desc": case["desc"],
                "expected": case["expected"], "actual": "error",
                "confidence": 0, "violations": 0,
                "checked_dimensions": [], "duration_ms": round((time.time() - start) * 1000),
                "passed": False, "verify_ok": False,
                "verify_dims": case.get("verify_dims", []),
                "is_fallback": False, "error": str(e),
            }


async def poll_task(
    client: httpx.AsyncClient, task_id: str, timeout: int = 120,
) -> dict:
    """轮询异步任务结果。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        await asyncio.sleep(3)
        try:
            resp = await client.get(
                f"{BASE_URL}/review/task/{task_id}",
                headers={"X-API-Key": API_KEY}, timeout=10.0,
            )
            data = resp.json()
            if data.get("status") == "completed":
                return data.get("result", {})
        except Exception:
            pass
    return {"verdict": "error", "error": "task_timeout"}


# ============================================================
# Main
# ============================================================

async def main():
    sys.stdout.reconfigure(encoding="utf-8")

    print(f"\n{'='*72}")
    print(f"  AdGuard Pro — 100条系统测试 (v3)")
    print(f"  时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  用例：{len(TEST_CASES)}条 | returned/review/pass 三态验证")
    print(f"{'='*72}\n")

    sync_cases = [c for c in TEST_CASES if not c.get("is_async")]
    async_cases = [c for c in TEST_CASES if c.get("is_async")]
    results = []
    semaphore = asyncio.Semaphore(2)

    async with httpx.AsyncClient() as client:
        # Sync cases
        print(f"\u63d0\u4ea4\u540c\u6b65\u7528\u4f8b\uff08{len(sync_cases)}\u6761\uff09...")
        tasks = [submit_case(client, c, semaphore) for c in sync_cases]
        sync_results = await asyncio.gather(*tasks)
        results.extend(sync_results)
        for r in sync_results:
            s = "\u2705" if r["passed"] else "\u274c"
            v = "\u2713" if r["verify_ok"] else "\u2717" if r["verify_dims"] else " "
            print(f"  {s} {r['case_id']:6s} | "
                  f"\u671f\u671b:{r['expected']:8s} \u5b9e\u9645:{r['actual']:8s} | "
                  f"conf:{r['confidence']:.2f} v:{r['violations']} "
                  f"dims:{len(r['checked_dimensions'])} "
                  f"[{v}] {r['duration_ms']}ms | {r['desc'][:32]}")

        # Async cases
        print(f"\n\u63d0\u4ea4\u5f02\u6b65\u89c6\u9891\u7528\u4f8b\uff08{len(async_cases)}\u6761\uff09...")
        for case in async_cases:
            print(f"  [{case['case_id']}] {case['desc'][:40]}...")
            result = await submit_case(client, case, semaphore)
            results.append(result)
            s = "\u2705" if result["passed"] else "\u274c"
            v = "\u2713" if result["verify_ok"] else "\u2717" if result["verify_dims"] else " "
            print(f"  {s} \u671f\u671b:{result['expected']:8s} \u5b9e\u9645:{result['actual']:8s} | "
                  f"conf:{result['confidence']:.2f} v:{result['violations']} "
                  f"dims:{len(result['checked_dimensions'])} [{v}] {result['duration_ms']}ms\n")

    # ════════ Summary ════════
    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    failed = total - passed

    returned_cases = [r for r in results if r["expected"] == "returned"]
    pass_cases = [r for r in results if r["expected"] == "pass"]
    review_cases = [r for r in results if r["expected"] == "review"]

    # FN: expected=returned but actual=pass
    false_neg = sum(1 for r in returned_cases if r["actual"] == "pass")
    # FP: expected=pass but actual in (returned, reject)
    false_pos = sum(1 for r in pass_cases if r["actual"] in ("returned", "reject"))

    # Verify dims
    verify_total = sum(1 for r in results if r["verify_dims"])
    verify_passed = sum(1 for r in results if r["verify_dims"] and r["verify_ok"])

    # Full check rate (4+ dimensions)
    full_check = sum(1 for r in results if len(r["checked_dimensions"]) >= 4)
    fallback_count = sum(1 for r in results if r["is_fallback"])

    # Returned count
    returned_actual = sum(1 for r in results if r["actual"] == "returned")

    modules = {
        "A-文案": [r for r in results if r["case_id"].startswith("T-A")],
        "B-图片": [r for r in results if r["case_id"].startswith("T-B")],
        "C-落地页": [r for r in results if r["case_id"].startswith("T-C")],
        "D-资质": [r for r in results if r["case_id"].startswith("T-D")],
        "E-视频": [r for r in results if r["case_id"].startswith("T-E")],
        "F-平台规范": [r for r in results if r["case_id"].startswith("T-F")],
        "G-综合": [r for r in results if r["case_id"].startswith("T-G")],
        "I-文案深化": [r for r in results if r["case_id"].startswith("T-I")],
        "J-资质深化": [r for r in results if r["case_id"].startswith("T-J")],
        "K-落地页深化": [r for r in results if r["case_id"].startswith("T-K")],
        "L-图片深化": [r for r in results if r["case_id"].startswith("T-L")],
        "M-视频深化": [r for r in results if r["case_id"].startswith("T-M")],
        "N-平台深化": [r for r in results if r["case_id"].startswith("T-N")],
        "O-一致性深化": [r for r in results if r["case_id"].startswith("T-O")],
        "P-综合深化": [r for r in results if r["case_id"].startswith("T-P")],
    }

    fn_status = "\u2705 \u8fbe\u6807" if false_neg == 0 else "\u274c \u8d85\u6807"
    fp_status = "\u2705 \u8fbe\u6807" if (not pass_cases or false_pos / len(pass_cases) <= 0.1) else "\u274c \u8d85\u6807"

    print(f"\n{'='*72}")
    print(f"  \u6d4b\u8bd5\u7ed3\u679c\u6c47\u603b")
    print(f"{'='*72}")
    print(f"  \u603b\u7528\u4f8b\uff1a{total} | \u901a\u8fc7\uff1a{passed} ({passed/total*100:.0f}%) | \u5931\u8d25\uff1a{failed}")
    print(f"  \u6f0f\u5ba1\u7387\uff1a{false_neg}/{len(returned_cases)} ({fn_status})")
    print(f"  \u8bef\u62d2\u7387\uff1a{false_pos}/{len(pass_cases)} ({fp_status})")
    print(f"  returned \u8f93\u51fa\uff1a{returned_actual}\u6761")
    print(f"  \u5168\u7ef4\u5ea6\u68c0\u6d4b(\u22654\u7ef4)\uff1a{full_check}/{total} ({full_check/total*100:.0f}%)")
    print(f"  \u964d\u7ea7\u7528\u4f8b\uff1a{fallback_count}\u6761")
    print(f"  \u7ef4\u5ea6\u9a8c\u8bc1\u70b9\uff1a{verify_passed}/{verify_total} "
          f"({verify_passed/verify_total*100:.0f}%)" if verify_total else "")

    print(f"\n  \u6309\u6a21\u5757\uff1a")
    for name, mrs in modules.items():
        p = sum(1 for r in mrs if r["passed"])
        t = len(mrs)
        bar = "\u2588" * p + "\u2591" * (t - p)
        print(f"    {name:8s} {bar} {p}/{t}")

    failed_cases = [r for r in results if not r["passed"]]
    if failed_cases:
        print(f"\n  \u5931\u8d25\u660e\u7ec6\uff1a")
        for r in failed_cases:
            extra = ""
            if r["verify_dims"] and not r["verify_ok"]:
                extra = f" [\u7ef4\u5ea6\u7f3a\u5931]"
            print(f"    \u274c {r['case_id']} \u671f\u671b:{r['expected']} "
                  f"\u5b9e\u9645:{r['actual']} conf:{r['confidence']:.2f}"
                  f"{extra} | {r['desc'][:35]}")

    # Save
    report = {
        "timestamp": datetime.now().isoformat(),
        "total": total, "passed": passed, "failed": failed,
        "false_negative_rate": false_neg / len(returned_cases) if returned_cases else 0,
        "false_positive_rate": false_pos / len(pass_cases) if pass_cases else 0,
        "returned_count": returned_actual,
        "full_check_rate": full_check / total,
        "verify_pass_rate": verify_passed / verify_total if verify_total else 0,
        "results": results,
    }
    path = f"evals/results/system_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n  \u62a5\u544a\u5df2\u4fdd\u5b58\uff1a{path}")
    print(f"{'='*72}\n")


if __name__ == "__main__":
    asyncio.run(main())
