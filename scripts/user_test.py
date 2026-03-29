"""
完整用户测试脚本
基于真实广告违规案例构建，覆盖6个品类40个测试用例
自动提交所有用例，生成测试报告
"""

import asyncio
import json
import sys
import time
from datetime import datetime

import httpx

sys.stdout.reconfigure(encoding="utf-8")

BASE_URL = "http://localhost:8000"
API_KEY = "test-key-001"

# ============================================================
# 测试用例：基于真实违规案例构建
# ============================================================

TEST_CASES = [

    # ========== 品类1：手机工具类 App（tool_app）==========
    {
        "case_id": "tool-001",
        "desc": "【违规】虚假功能宣传-定位类App",
        "expected": "reject",
        "data": {
            "request_id": "ut-tool-001",
            "advertiser_id": "adv-tool-test",
            "ad_category": "tool_app",
            "creative_type": "text",
            "content": {
                "title": "隐秘定位追踪，不需要对方同意",
                "description": "无感知定位，对方完全察觉不到，实时掌握位置动态",
                "cta_text": "立即下载"
            }
        }
    },
    {
        "case_id": "tool-002",
        "desc": "【违规】伪造系统告警诱导安装",
        "expected": "reject",
        "data": {
            "request_id": "ut-tool-002",
            "advertiser_id": "adv-tool-test",
            "ad_category": "tool_app",
            "creative_type": "text",
            "content": {
                "title": "系统检测到您的手机存在严重安全威胁",
                "description": "您的手机已感染病毒，立即安装修复，否则将造成数据泄露",
                "cta_text": "立即修复"
            }
        }
    },
    {
        "case_id": "tool-003",
        "desc": "【违规】绝对化用语-最好最强",
        "expected": "reject",
        "data": {
            "request_id": "ut-tool-003",
            "advertiser_id": "adv-tool-test",
            "ad_category": "tool_app",
            "creative_type": "text",
            "content": {
                "title": "全网最好用的手机清理工具，第一名的清理效果",
                "description": "唯一采用AI深度清理技术，彻底清除垃圾文件",
                "cta_text": "免费下载"
            }
        }
    },
    {
        "case_id": "tool-004",
        "desc": "【违规】虚假下载量数据",
        "expected": "reject",
        "data": {
            "request_id": "ut-tool-004",
            "advertiser_id": "adv-tool-test",
            "ad_category": "tool_app",
            "creative_type": "text",
            "content": {
                "title": "手机加速工具",
                "description": "全球超过10亿用户的选择，好评率高达99.9%，4.9分满分评价",
                "cta_text": "加入10亿用户"
            }
        }
    },
    {
        "case_id": "tool-005",
        "desc": "【合规】正常工具类广告",
        "expected": "pass",
        "data": {
            "request_id": "ut-tool-005",
            "advertiser_id": "adv-tool-test",
            "ad_category": "tool_app",
            "creative_type": "text",
            "content": {
                "title": "手机清理助手",
                "description": "一键清理缓存文件，释放存储空间，让手机运行更流畅",
                "cta_text": "免费下载"
            }
        }
    },

    # ========== 品类2：金融理财（finance）==========
    {
        "case_id": "fin-001",
        "desc": "【违规】保本保息承诺",
        "expected": "reject",
        "data": {
            "request_id": "ut-fin-001",
            "advertiser_id": "adv-fin-test",
            "ad_category": "finance",
            "creative_type": "text",
            "content": {
                "title": "稳赚不赔的理财产品，保本保息",
                "description": "年化收益8%-15%，本金安全保障，到期100%兑付，零风险投资首选",
                "cta_text": "立即投资"
            }
        }
    },
    {
        "case_id": "fin-002",
        "desc": "【违规】承诺固定收益回报",
        "expected": "reject",
        "data": {
            "request_id": "ut-fin-002",
            "advertiser_id": "adv-fin-test",
            "ad_category": "finance",
            "creative_type": "text",
            "content": {
                "title": "每月固定收益，保证月入5000+",
                "description": "签约即锁定收益，无论市场涨跌，每月准时到账，已有50万人获益",
                "cta_text": "马上赚钱"
            }
        }
    },
    {
        "case_id": "fin-003",
        "desc": "【违规】无金融牌照未声明风险",
        "expected": "reject",
        "data": {
            "request_id": "ut-fin-003",
            "advertiser_id": "adv-fin-test",
            "ad_category": "finance",
            "creative_type": "text",
            "content": {
                "title": "专业理财顾问，帮您实现财富自由",
                "description": "独家内部消息，精准选股，过去3年客户平均收益率达200%",
                "cta_text": "免费咨询"
            }
        }
    },
    {
        "case_id": "fin-004",
        "desc": "【边界】历史业绩宣传",
        "expected": "review",
        "data": {
            "request_id": "ut-fin-004",
            "advertiser_id": "adv-fin-test",
            "ad_category": "finance",
            "creative_type": "text",
            "content": {
                "title": "专业资产管理，历史业绩优良",
                "description": "10年深耕价值投资，历史年化回报稳健，长期陪伴客户财富成长",
                "cta_text": "了解更多"
            }
        }
    },
    {
        "case_id": "fin-005",
        "desc": "【合规】含风险提示的金融广告",
        "expected": "pass",
        "data": {
            "request_id": "ut-fin-005",
            "advertiser_id": "adv-fin-test",
            "ad_category": "finance",
            "creative_type": "text",
            "content": {
                "title": "专业理财服务",
                "description": "多元化资产配置方案，专业投资顾问1对1服务。投资有风险，入市需谨慎。",
                "cta_text": "预约咨询"
            }
        }
    },

    # ========== 品类3：医疗健康（health）==========
    {
        "case_id": "health-001",
        "desc": "【违规】宣称治愈疾病",
        "expected": "reject",
        "data": {
            "request_id": "ut-health-001",
            "advertiser_id": "adv-health-test",
            "ad_category": "health",
            "creative_type": "text",
            "content": {
                "title": "糖尿病根治神器，彻底告别胰岛素",
                "description": "临床验证有效率98%，服用3个月血糖恢复正常，已帮助10万糖尿病患者康复",
                "cta_text": "立即购买"
            }
        }
    },
    {
        "case_id": "health-002",
        "desc": "【违规】医疗器械无批准文号",
        "expected": "reject",
        "data": {
            "request_id": "ut-health-002",
            "advertiser_id": "adv-health-test",
            "ad_category": "health",
            "creative_type": "text",
            "content": {
                "title": "近视治疗仪，一个月恢复视力",
                "description": "专利技术，修复受损视网膜，近视度数每月降低100度，无需手术",
                "cta_text": "免费体验"
            }
        }
    },
    {
        "case_id": "health-003",
        "desc": "【违规】保健品宣称药物效果",
        "expected": "reject",
        "data": {
            "request_id": "ut-health-003",
            "advertiser_id": "adv-health-test",
            "ad_category": "health",
            "creative_type": "text",
            "content": {
                "title": "天然植物提取，专治失眠焦虑",
                "description": "代替安眠药，无依赖无副作用，当晚见效，深度睡眠有保障",
                "cta_text": "立即抢购"
            }
        }
    },
    {
        "case_id": "health-004",
        "desc": "【违规】性暗示低俗营销",
        "expected": "reject",
        "data": {
            "request_id": "ut-health-004",
            "advertiser_id": "adv-health-test",
            "ad_category": "health",
            "creative_type": "text",
            "content": {
                "title": "今夜，让你难以抗拒的能量补充",
                "description": "男性专属秘方，效果让她惊喜，持久战斗力的秘密武器",
                "cta_text": "私信了解"
            }
        }
    },
    {
        "case_id": "health-005",
        "desc": "【合规】正规健康类App广告",
        "expected": "pass",
        "data": {
            "request_id": "ut-health-005",
            "advertiser_id": "adv-health-test",
            "ad_category": "health",
            "creative_type": "text",
            "content": {
                "title": "健康管理助手",
                "description": "记录每日运动和饮食，科学制定健康计划，养成良好生活习惯",
                "cta_text": "开始健康生活"
            }
        }
    },

    # ========== 品类4：手机游戏（game）==========
    {
        "case_id": "game-001",
        "desc": "【违规】高额充值返利诱导",
        "expected": "reject",
        "data": {
            "request_id": "ut-game-001",
            "advertiser_id": "adv-game-test",
            "ad_category": "game",
            "creative_type": "text",
            "content": {
                "title": "充值1000送2000，月入万元不是梦",
                "description": "充值返利高达200%，公会战队瓜分百万奖池，躺赚首选游戏",
                "cta_text": "立即充值"
            },
            "advertiser_qualification_ids": ["ISBN978-7-100-00001"]
        }
    },
    {
        "case_id": "game-002",
        "desc": "【违规】诱导未成年人消费",
        "expected": "reject",
        "data": {
            "request_id": "ut-game-002",
            "advertiser_id": "adv-game-test",
            "ad_category": "game",
            "creative_type": "text",
            "content": {
                "title": "小学生都在玩的游戏，妈妈不让玩更要玩",
                "description": "每天充值5元，成为全班最厉害的人，同学都会羡慕你",
                "cta_text": "偷偷下载"
            },
            "advertiser_qualification_ids": ["ISBN978-7-100-00001"]
        }
    },
    {
        "case_id": "game-003",
        "desc": "【违规】无版号游戏投放",
        "expected": "reject",
        "data": {
            "request_id": "ut-game-003",
            "advertiser_id": "adv-game-test",
            "ad_category": "game",
            "creative_type": "text",
            "content": {
                "title": "全新策略手游，史诗级战争体验",
                "description": "百万玩家同屏对战，精美3D画面，现已开放公测",
                "cta_text": "免费下载"
            }
        }
    },
    {
        "case_id": "game-004",
        "desc": "【边界】丰厚奖励模糊表述",
        "expected": "review",
        "data": {
            "request_id": "ut-game-004",
            "advertiser_id": "adv-game-test",
            "ad_category": "game",
            "creative_type": "text",
            "content": {
                "title": "登录即送丰厚大礼，每日奖励等你领",
                "description": "新服开区，首充有惊喜，活跃玩家专属福利",
                "cta_text": "领取奖励"
            },
            "advertiser_qualification_ids": ["ISBN978-7-100-00001"]
        }
    },
    {
        "case_id": "game-005",
        "desc": "【合规】正规游戏广告含版号",
        "expected": "pass",
        "data": {
            "request_id": "ut-game-005",
            "advertiser_id": "adv-game-test",
            "ad_category": "game",
            "creative_type": "text",
            "content": {
                "title": "经典三国策略手游",
                "description": "重现三国历史，带领英雄征战天下，与好友组队共创霸业",
                "cta_text": "立即下载"
            },
            "advertiser_qualification_ids": ["ISBN978-7-100-00001"]
        }
    },

    # ========== 品类5：教育培训（education）==========
    {
        "case_id": "edu-001",
        "desc": "【违规】保证就业承诺",
        "expected": "reject",
        "data": {
            "request_id": "ut-edu-001",
            "advertiser_id": "adv-edu-test",
            "ad_category": "education",
            "creative_type": "text",
            "content": {
                "title": "学完保证就业，月薪3万起步",
                "description": "签订就业协议，不就业全额退款，已帮助5000名学员入职大厂",
                "cta_text": "免费试听"
            }
        }
    },
    {
        "case_id": "edu-002",
        "desc": "【违规】虚假通过率宣传",
        "expected": "reject",
        "data": {
            "request_id": "ut-edu-002",
            "advertiser_id": "adv-edu-test",
            "ad_category": "education",
            "creative_type": "text",
            "content": {
                "title": "考研辅导，100%上岸保障班",
                "description": "通过率高达98%，全国第一考研机构，名师亲授，不过退全款",
                "cta_text": "立即报名"
            }
        }
    },
    {
        "case_id": "edu-003",
        "desc": "【违规】学科培训无资质宣传",
        "expected": "reject",
        "data": {
            "request_id": "ut-edu-003",
            "advertiser_id": "adv-edu-test",
            "ad_category": "education",
            "creative_type": "text",
            "content": {
                "title": "小学数学一对一辅导，成绩提升保证",
                "description": "清北名师授课，期末成绩提高30分，全市最专业的学科培训",
                "cta_text": "预约试课"
            }
        }
    },
    {
        "case_id": "edu-004",
        "desc": "【边界】效果宣传模糊表述",
        "expected": "review",
        "data": {
            "request_id": "ut-edu-004",
            "advertiser_id": "adv-edu-test",
            "ad_category": "education",
            "creative_type": "text",
            "content": {
                "title": "职业技能培训，助力职场晋升",
                "description": "系统化课程体系，学员普遍反映能力提升明显，深受企业欢迎",
                "cta_text": "了解课程"
            }
        }
    },
    {
        "case_id": "edu-005",
        "desc": "【合规】正规职业技能培训",
        "expected": "pass",
        "data": {
            "request_id": "ut-edu-005",
            "advertiser_id": "adv-edu-test",
            "ad_category": "education",
            "creative_type": "text",
            "content": {
                "title": "Python编程入门课程",
                "description": "从零基础到项目实战，120课时系统学习，配套练习题和项目案例",
                "cta_text": "免费试学"
            }
        }
    },

    # ========== 品类6：电商（ecommerce）==========
    {
        "case_id": "ecom-001",
        "desc": "【违规】虚构原价价格误导",
        "expected": "reject",
        "data": {
            "request_id": "ut-ecom-001",
            "advertiser_id": "adv-ecom-test",
            "ad_category": "ecommerce",
            "creative_type": "text",
            "content": {
                "title": "限时特惠，原价999现价99",
                "description": "全国最低价保证，比官方旗舰店便宜80%，正品授权，假一赔十",
                "cta_text": "抢购"
            }
        }
    },
    {
        "case_id": "ecom-002",
        "desc": "【违规】宣扬攀比不良风气",
        "expected": "reject",
        "data": {
            "request_id": "ut-ecom-002",
            "advertiser_id": "adv-ecom-test",
            "ad_category": "ecommerce",
            "creative_type": "text",
            "content": {
                "title": "别人爸爸送名牌，你爸爸买什么",
                "description": "孩子的面子就是你的面子，给孩子最好的，不让孩子输在起跑线上",
                "cta_text": "立即购买"
            }
        }
    },
    {
        "case_id": "ecom-003",
        "desc": "【违规】极限词+虚假销量",
        "expected": "reject",
        "data": {
            "request_id": "ut-ecom-003",
            "advertiser_id": "adv-ecom-test",
            "ad_category": "ecommerce",
            "creative_type": "text",
            "content": {
                "title": "全球最好的护肤品，销量第一",
                "description": "全球销量突破1亿件，荣获国际最高品质奖，唯一获得诺贝尔奖推荐的护肤品",
                "cta_text": "立即抢购"
            }
        }
    },
    {
        "case_id": "ecom-004",
        "desc": "【边界】夸张但模糊的效果描述",
        "expected": "review",
        "data": {
            "request_id": "ut-ecom-004",
            "advertiser_id": "adv-ecom-test",
            "ad_category": "ecommerce",
            "creative_type": "text",
            "content": {
                "title": "超神奇面膜，用了都说好",
                "description": "深层补水效果显著，用户反馈皮肤变化明显，回购率超高",
                "cta_text": "查看详情"
            }
        }
    },
    {
        "case_id": "ecom-005",
        "desc": "【合规】正常电商广告",
        "expected": "pass",
        "data": {
            "request_id": "ut-ecom-005",
            "advertiser_id": "adv-ecom-test",
            "ad_category": "ecommerce",
            "creative_type": "text",
            "content": {
                "title": "天然植物精华护肤套装",
                "description": "甄选天然植物成分，温和不刺激，适合敏感肌肤，新用户首单立减50元",
                "cta_text": "立即选购"
            }
        }
    },

    # ========== 对抗性测试：规避检测变体 ==========
    {
        "case_id": "adv-001",
        "desc": "【对抗】谐音绕过违禁词",
        "expected": "reject",
        "data": {
            "request_id": "ut-adv-001",
            "advertiser_id": "adv-adversarial",
            "ad_category": "tool_app",
            "creative_type": "text",
            "content": {
                "title": "zui好用的清理工具，Di一名体验",
                "description": "wéi yī 采用AI技术，效果bǎo zhèng",
                "cta_text": "下载"
            }
        }
    },
    {
        "case_id": "adv-002",
        "desc": "【对抗】符号插入绕过检测",
        "expected": "reject",
        "data": {
            "request_id": "ut-adv-002",
            "advertiser_id": "adv-adversarial",
            "ad_category": "finance",
            "creative_type": "text",
            "content": {
                "title": "理财产品，保.本.保.息，稳.赚.不.赔",
                "description": "零★风★险，年化收益8%，1○○%安全",
                "cta_text": "投资"
            }
        }
    },
    {
        "case_id": "adv-003",
        "desc": "【对抗】emoji混入违禁词",
        "expected": "reject",
        "data": {
            "request_id": "ut-adv-003",
            "advertiser_id": "adv-adversarial",
            "ad_category": "ecommerce",
            "creative_type": "text",
            "content": {
                "title": "全球\U0001f30d最\U0001f525好用护肤品，销量\U0001f3c6第一",
                "description": "唯\U0001f48e一获得国际大奖，效果\U0001f4af保证",
                "cta_text": "购买"
            }
        }
    },
    {
        "case_id": "adv-004",
        "desc": "【对抗】同义替换绕过违禁词",
        "expected": "reject",
        "data": {
            "request_id": "ut-adv-004",
            "advertiser_id": "adv-adversarial",
            "ad_category": "tool_app",
            "creative_type": "text",
            "content": {
                "title": "业界翘楚，行业标杆，众多用户首选",
                "description": "在同类产品中遥遥领先，无出其右，各大媒体一致推荐",
                "cta_text": "下载"
            }
        }
    },
    {
        "case_id": "adv-005",
        "desc": "【对抗】拆字绕过检测",
        "expected": "reject",
        "data": {
            "request_id": "ut-adv-005",
            "advertiser_id": "adv-adversarial",
            "ad_category": "finance",
            "creative_type": "text",
            "content": {
                "title": "理财收益有保 证，本金有保 障",
                "description": "零 风 险投资，收益有 保 证，不怕市场波动",
                "cta_text": "立即投资"
            }
        }
    },

    # ========== 图片+文案混合测试 ==========
    {
        "case_id": "img-001",
        "desc": "【合规】图文混合-工具类",
        "expected": "pass",
        "data": {
            "request_id": "ut-img-001",
            "advertiser_id": "adv-img-test",
            "ad_category": "tool_app",
            "creative_type": "mixed",
            "content": {
                "title": "手机管家",
                "description": "智能管理手机性能，保护隐私安全",
                "cta_text": "免费下载",
                "image_urls": [
                    "https://www.baidu.com/img/PCtm_d9c8750bed0b3c7d089fa7d55720d6cf.png"
                ]
            }
        }
    },
    {
        "case_id": "img-002",
        "desc": "【违规】图文混合-违规文案",
        "expected": "reject",
        "data": {
            "request_id": "ut-img-002",
            "advertiser_id": "adv-img-test",
            "ad_category": "health",
            "creative_type": "mixed",
            "content": {
                "title": "最好的保健品，专治百病",
                "description": "根治糖尿病、高血压，服用一周见效，保证有效",
                "cta_text": "立即购买",
                "image_urls": [
                    "https://www.baidu.com/img/PCtm_d9c8750bed0b3c7d089fa7d55720d6cf.png"
                ]
            }
        }
    },
]


async def run_test(client: httpx.AsyncClient, case: dict) -> dict:
    """执行单个测试用例。"""
    start = time.time()
    try:
        resp = await client.post(
            f"{BASE_URL}/review",
            json=case["data"],
            headers={"X-API-Key": API_KEY},
            timeout=60.0,
        )
        result = resp.json()
        duration = round((time.time() - start) * 1000)

        actual = result.get("verdict", "error")
        expected = case["expected"]

        # 判断是否通过：
        # pass/reject 必须精确匹配
        # review 可以接受 review 或更保守的 reject
        if expected == "review":
            passed = actual in ("review", "reject")
        else:
            passed = actual == expected

        return {
            "case_id": case["case_id"],
            "desc": case["desc"],
            "expected": expected,
            "actual": actual,
            "confidence": result.get("confidence", 0),
            "violations": len(result.get("violations", [])),
            "duration_ms": duration,
            "passed": passed,
            "verdict_match": actual == expected,
        }
    except Exception as e:
        return {
            "case_id": case["case_id"],
            "desc": case["desc"],
            "expected": case["expected"],
            "actual": "error",
            "confidence": 0,
            "violations": 0,
            "duration_ms": round((time.time() - start) * 1000),
            "passed": False,
            "verdict_match": False,
            "error": str(e),
        }


async def main():
    print(f"\n{'='*70}")
    print(f"  广告素材合规审核 Agent — 完整用户测试")
    print(f"  测试时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  测试用例：{len(TEST_CASES)} 条")
    print(f"{'='*70}\n")

    results = []

    async with httpx.AsyncClient() as client:
        for i, case in enumerate(TEST_CASES, 1):
            print(f"[{i:02d}/{len(TEST_CASES)}] {case['case_id']} {case['desc'][:40]}...")
            result = await run_test(client, case)
            results.append(result)

            status = "\u2705 PASS" if result["passed"] else "\u274c FAIL"
            match_str = "=" if result["verdict_match"] else f"\u2260(expect {result['expected']})"
            print(f"       {status} | verdict: {result['actual']}{match_str} | "
                  f"confidence: {result['confidence']:.2f} | "
                  f"violations: {result['violations']} | "
                  f"{result['duration_ms']}ms\n")

            await asyncio.sleep(0.5)

    # ========== 生成测试报告 ==========
    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    failed = total - passed

    by_category = {}
    for r in results:
        cat = r["case_id"].split("-")[0]
        if cat not in by_category:
            by_category[cat] = {"total": 0, "passed": 0}
        by_category[cat]["total"] += 1
        if r["passed"]:
            by_category[cat]["passed"] += 1

    reject_cases = [r for r in results if r["expected"] == "reject"]
    pass_cases = [r for r in results if r["expected"] == "pass"]
    review_cases = [r for r in results if r["expected"] == "review"]

    false_negative = sum(1 for r in reject_cases if r["actual"] == "pass")
    false_positive = sum(1 for r in pass_cases if r["actual"] == "reject")

    avg_duration = sum(r["duration_ms"] for r in results) / total

    print(f"\n{'='*70}")
    print(f"  测试报告汇总")
    print(f"{'='*70}")
    print(f"  总用例数:     {total}")
    print(f"  通过:         {passed} ({passed/total*100:.1f}%)")
    print(f"  失败:         {failed} ({failed/total*100:.1f}%)")
    fn_rate = false_negative / len(reject_cases) * 100 if reject_cases else 0
    fp_rate = false_positive / len(pass_cases) * 100 if pass_cases else 0
    fn_status = "\u2705 达标" if fn_rate <= 5 else "\u274c 超标"
    fp_status = "\u2705 达标" if fp_rate <= 10 else "\u274c 超标"
    print(f"  漏审率(FN):   {false_negative}/{len(reject_cases)} ({fn_rate:.1f}%) {fn_status}")
    print(f"  误拒率(FP):   {false_positive}/{len(pass_cases)} ({fp_rate:.1f}%) {fp_status}")
    print(f"  平均耗时:     {avg_duration:.0f}ms")

    print(f"\n  按品类明细：")
    cat_names = {
        "tool": "工具类App", "fin": "金融理财",
        "health": "医疗健康", "game": "手机游戏",
        "edu": "教育培训", "ecom": "电商",
        "adv": "对抗性测试", "img": "图文混合"
    }
    for cat, stat in by_category.items():
        name = cat_names.get(cat, cat)
        rate = stat["passed"] / stat["total"] * 100
        bar = "\u2588" * int(rate / 10) + "\u2591" * (10 - int(rate / 10))
        print(f"    {name:10s}  {bar}  {stat['passed']}/{stat['total']} ({rate:.0f}%)")

    failed_cases = [r for r in results if not r["passed"]]
    if failed_cases:
        print(f"\n  失败用例明细：")
        for r in failed_cases:
            print(f"    \u274c {r['case_id']} | expect:{r['expected']} actual:{r['actual']} | {r['desc'][:45]}")

    report = {
        "timestamp": datetime.now().isoformat(),
        "summary": {
            "total": total, "passed": passed, "failed": failed,
            "false_negative_rate": false_negative / len(reject_cases) if reject_cases else 0,
            "false_positive_rate": false_positive / len(pass_cases) if pass_cases else 0,
            "avg_duration_ms": avg_duration,
        },
        "results": results,
    }
    report_path = f"evals/results/user_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n  报告已保存：{report_path}")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    asyncio.run(main())
