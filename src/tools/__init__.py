# Tool 导出，每添加一个 Tool 在这里注册
# 各 Tool 按需导入，避免 cv2 等重依赖在无需时加载：
#   from src.tools.text_checker import TextViolationChecker
#   from src.tools.image_checker import ImageContentChecker
#   from src.tools.landing_page import LandingPageChecker
#   from src.tools.qualification import QualificationChecker
#   from src.tools.platform_rule import PlatformRuleChecker
#   from src.tools.consistency_checker import ConsistencyChecker
