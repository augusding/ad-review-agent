"""
代码库健康检查。

检查配置文件、代码、Prompt 之间的一致性。
全部通过 → exit 0；有问题 → 输出错误 + exit 1。

用法：
    python scripts/cleanup_check.py
"""
import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def check_constraints_tool_names() -> list[str]:
    """
    检查 constraints.yaml 中引用的 tool_name 与 src/tools/ 中的类匹配。

    Returns:
        错误消息列表
    """
    errors = []

    # 从 src/tools/ 收集所有继承 BaseTool 的类名
    tools_dir = PROJECT_ROOT / "src" / "tools"
    tool_classes: set[str] = set()
    for py_file in tools_dir.glob("*.py"):
        if py_file.name.startswith("_") or py_file.name == "__init__.py":
            continue
        content = py_file.read_text(encoding="utf-8")
        for match in re.finditer(r"class\s+(\w+)\s*\(.*BaseTool.*\)", content):
            tool_classes.add(match.group(1))

    # 从 constraints.yaml 提取 tool_name
    constraints_path = PROJECT_ROOT / "harness" / "constraints.yaml"
    if not constraints_path.exists():
        errors.append("harness/constraints.yaml not found")
        return errors

    content = constraints_path.read_text(encoding="utf-8")
    yaml_tool_names: list[str] = re.findall(r'tool_name:\s*"(\w+)"', content)

    for name in yaml_tool_names:
        if name not in tool_classes:
            errors.append(
                f"constraints.yaml references tool_name '{name}' "
                f"but no matching BaseTool subclass found in src/tools/. "
                f"Available: {sorted(tool_classes)}"
            )

    return errors


def check_router_env_vars() -> list[str]:
    """
    检查 model_router.yaml 中引用的 env 变量在 .env.example 有对应。

    Returns:
        错误消息列表
    """
    errors = []

    router_path = PROJECT_ROOT / "harness" / "model_router.yaml"
    if not router_path.exists():
        errors.append("harness/model_router.yaml not found")
        return errors

    env_example_path = PROJECT_ROOT / ".env.example"
    if not env_example_path.exists():
        errors.append(".env.example not found")
        return errors

    # 提取 .env.example 中定义的变量名
    env_content = env_example_path.read_text(encoding="utf-8")
    defined_vars: set[str] = set()
    for line in env_content.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            var_name = line.split("=", 1)[0].strip()
            defined_vars.add(var_name)
    # 也包括注释掉的可选变量 (# VAR=value)
    for match in re.finditer(r"^#\s*(\w+)=", env_content, re.MULTILINE):
        defined_vars.add(match.group(1))

    # 提取 model_router.yaml 中的 _env 引用
    router_content = router_path.read_text(encoding="utf-8")
    env_refs = re.findall(r'(?:api_key_env|base_url_env):\s*"(\w+)"', router_content)

    for var in env_refs:
        if var not in defined_vars:
            errors.append(
                f"model_router.yaml references env var '{var}' "
                f"but it is not defined in .env.example"
            )

    return errors


def check_feature_list() -> list[str]:
    """
    检查 feature_list.json 格式正确。

    Returns:
        错误消息列表
    """
    errors = []

    fpath = PROJECT_ROOT / "feature_list.json"
    if not fpath.exists():
        errors.append("feature_list.json not found")
        return errors

    try:
        data = json.loads(fpath.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        errors.append(f"feature_list.json is invalid JSON: {e}")
        return errors

    if not isinstance(data, list):
        errors.append("feature_list.json root must be a JSON array")
        return errors

    required_keys = {"id", "category", "priority", "description", "passes"}
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            errors.append(f"feature_list.json[{i}] is not an object")
            continue
        missing = required_keys - set(item.keys())
        if missing:
            errors.append(
                f"feature_list.json[{i}] (id={item.get('id', '?')}) "
                f"missing keys: {sorted(missing)}"
            )
        if "passes" in item and not isinstance(item["passes"], bool):
            errors.append(
                f"feature_list.json[{i}] (id={item.get('id', '?')}) "
                f"'passes' must be boolean, got {type(item['passes']).__name__}"
            )

    return errors


def check_prompt_versions() -> list[str]:
    """
    检查 src/prompts/ 下的文件头部有版本注释。

    Returns:
        错误消息列表
    """
    errors = []

    prompts_dir = PROJECT_ROOT / "src" / "prompts"
    if not prompts_dir.exists():
        errors.append("src/prompts/ directory not found")
        return errors

    for txt_file in sorted(prompts_dir.glob("*.txt")):
        content = txt_file.read_text(encoding="utf-8")
        first_line = content.split("\n", 1)[0].strip()
        if not re.match(r"#\s*Version:\s*\d", first_line):
            errors.append(
                f"src/prompts/{txt_file.name} missing version header "
                f"(expected '# Version: x.x' on first line, "
                f"got: '{first_line[:60]}')"
            )

    return errors


def main() -> int:
    """
    运行所有健康检查。

    Returns:
        0=全部通过，1=有错误
    """
    checks = [
        ("constraints.yaml tool_name references", check_constraints_tool_names),
        ("model_router.yaml env var references", check_router_env_vars),
        ("feature_list.json format", check_feature_list),
        ("prompt version headers", check_prompt_versions),
    ]

    all_errors: list[str] = []
    print("=" * 60)
    print("  Codebase Health Check")
    print("=" * 60)

    for label, check_fn in checks:
        errors = check_fn()
        status = "PASS" if not errors else "FAIL"
        print(f"  [{status}] {label}")
        for err in errors:
            print(f"         {err}")
        all_errors.extend(errors)

    print("")
    if all_errors:
        print(f"  {len(all_errors)} error(s) found.")
        print("=" * 60)
        return 1
    else:
        print("  All checks passed.")
        print("=" * 60)
        return 0


if __name__ == "__main__":
    sys.exit(main())
