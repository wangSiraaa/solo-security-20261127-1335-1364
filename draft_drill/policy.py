"""管理员策略：批准的是组件组合及其交互能力，而不只是单个组件。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .model import Component, Order, PolicyDecision


@dataclass(frozen=True)
class ApprovedCombination:
    combination_id: str
    core_coordinates: frozenset[str]
    interaction_requirements: dict[str, str]
    classifications: frozenset[str]
    note: str = ""


class PolicyStore:
    def __init__(
        self,
        components: list[Component],
        combinations: list[ApprovedCombination],
    ) -> None:
        self.catalog = {item.coordinate: item for item in components}
        self.combinations = combinations

    def evaluate(
        self,
        module_coordinates: list[str] | tuple[str, ...],
        interaction_claims: dict[str, str],
        order: Optional[Order] = None,
    ) -> PolicyDecision:
        coordinates = list(module_coordinates)

        unknown = [c for c in coordinates if c not in self.catalog]
        if unknown:
            return PolicyDecision(
                ok=False,
                code="UNKNOWN_MEASUREMENT",
                summary="存在未登记/未单独获准的组件度量",
                reasons=tuple(f"未登记组件：{c}" for c in unknown),
                details={"unknown": unknown},
            )

        active = [self.catalog[c] for c in coordinates]
        individually_revoked = [c.coordinate for c in active if not c.individually_approved]
        if individually_revoked:
            return PolicyDecision(
                ok=False,
                code="COMPONENT_REVOKED",
                summary="某个组件的单独合格资格已被撤销",
                reasons=tuple(f"已撤销组件：{c}" for c in individually_revoked),
            )

        attachments = [c.coordinate for c in active if c.kind == "attachment"]
        core_coordinates = frozenset(c.coordinate for c in active if c.kind != "attachment")

        combination = next(
            (item for item in self.combinations if item.core_coordinates == core_coordinates),
            None,
        )
        individually_approved = all(self.catalog[c].individually_approved for c in coordinates)

        if combination is None:
            pair = sorted(core_coordinates)
            reasons = [
                "字库和渲染器可能分别在组件白名单中，但管理员没有批准这一整组搭配。",
                f"当前核心组件：{', '.join(pair) if pair else '(空)'}",
            ]
            if {
                "FONT-KAI@9.0.0",
                "RENDERER-XIN@6.1.0",
            }.issubset(core_coordinates):
                reasons.append(
                    "该搭配会启用楷体动态提示到 XIN 原生 hint 桥的交互；"
                    "管理员从未批准这条原生交互路径。"
                )
            return PolicyDecision(
                ok=False,
                code="NO_APPROVED_COMBINATION",
                summary="当前组件组合或其交互能力不在管理员批准清单内",
                reasons=tuple(reasons),
                details={
                    "core_coordinates": sorted(core_coordinates),
                    "individually_approved": individually_approved,
                    "attachments": attachments,
                },
            )

        if order is not None and order.required_attachment not in coordinates:
            return PolicyDecision(
                ok=False,
                code="MISSING_REQUIRED_ATTACHMENT",
                summary="批准的核心组件已恢复，但订单要求的隔屏阅读附件不在目标工位",
                reasons=(
                    f"订单 {order.order_id} 要求附件 {order.required_attachment}。",
                    "环境核心组合合格，不等于本订单的交付条件齐全。",
                ),
                combination_id=combination.combination_id,
                details={
                    "required_attachment": order.required_attachment,
                    "present_attachments": attachments,
                },
            )

        mismatches: list[str] = []
        for key, required_value in combination.interaction_requirements.items():
            actual = interaction_claims.get(key, "<未报告>")
            if actual != required_value:
                mismatches.append(f"{key}: 要求 {required_value}，实际 {actual}")
        if mismatches:
            return PolicyDecision(
                ok=False,
                code="INTERACTION_POLICY_MISMATCH",
                summary="组件组合虽已登记，但实际交互能力不符合批准范围",
                reasons=tuple(mismatches),
                combination_id=combination.combination_id,
                details={"claims": dict(interaction_claims)},
            )

        if order is not None and order.required_attachment:
            separated_path = interaction_claims.get("separated_screen_path")
            if separated_path != "enforced":
                return PolicyDecision(
                    ok=False,
                    code="SEPARATED_SCREEN_INTERACTION_NOT_ENFORCED",
                    summary="附件虽在清单中，但隔屏阅读交互路径未处于强制状态",
                    reasons=(
                        f"separated_screen_path: 要求 enforced，实际 {separated_path!r}",
                    ),
                    combination_id=combination.combination_id,
                )

        if order is not None and order.classification not in combination.classifications:
            return PolicyDecision(
                ok=False,
                code="CLASSIFICATION_NOT_APPROVED_FOR_COMBINATION",
                summary="该组合未获准处理此密级/类别的受限底稿",
                reasons=(
                    f"订单密级 {order.classification}；"
                    f"该组合仅获准：{', '.join(sorted(combination.classifications))}",
                ),
                combination_id=combination.combination_id,
            )

        return PolicyDecision(
            ok=True,
            code="APPROVED",
            summary=f"组合 {combination.combination_id} 及其交互能力已获准",
            combination_id=combination.combination_id,
            details={
                "attachments": attachments,
                "note": combination.note,
            },
        )


def standard_components() -> list[Component]:
    """演练目录：关键替换件都“单独合格”，但坏搭配没有组合批文。"""

    return [
        Component("FONT-SONG", "font", "2.4.1"),
        Component("RENDERER-PRESS", "renderer", "4.0.2"),
        Component("ATTACH-SCREEN", "attachment", "1.1.0"),
        # 两个将被换入的组件：各自都合格。
        Component("FONT-KAI", "font", "9.0.0"),
        Component("RENDERER-XIN", "renderer", "6.1.0"),
        # 它们各自与别的搭档有已批准组合，避免把“单独合格”写成空话。
        Component("RENDERER-CLASSIC", "renderer", "3.8.1"),
        Component("FONT-HEI", "font", "5.2.0"),
    ]


def standard_policy() -> PolicyStore:
    components = standard_components()
    by_id = {c.comp_id: c for c in components}

    def coords(*names: str) -> frozenset[str]:
        return frozenset(by_id[name].coordinate for name in names)

    restricted_interactions = {
        "native_font_code": "blocked",
        "shaper_sandbox": "enforced",
        "renderer_plugin_bridge": "disabled",
        "clipboard_export": "blocked",
    }

    combinations = [
        ApprovedCombination(
            combination_id="COMBO-SONG-PRESS-RESTRICTED-2026Q4",
            core_coordinates=coords("FONT-SONG", "RENDERER-PRESS"),
            interaction_requirements=restricted_interactions,
            classifications=frozenset({"restricted-draft", "night-shift-sample"}),
            note="夜班受限排稿基准组合；必须连接隔屏阅读附件。",
        ),
        ApprovedCombination(
            combination_id="COMBO-KAI-CLASSIC-SANDBOXED",
            core_coordinates=coords("FONT-KAI", "RENDERER-CLASSIC"),
            interaction_requirements={
                "native_font_code": "dynamic_hints_sandboxed",
                "shaper_sandbox": "enforced",
                "renderer_plugin_bridge": "disabled",
                "clipboard_export": "blocked",
            },
            classifications=frozenset({"internal-draft"}),
            note="楷体可以使用，但只能与关闭原生桥的 CLASSIC 渲染器搭配。",
        ),
        ApprovedCombination(
            combination_id="COMBO-HEI-XIN-VERIFIED-TABLES",
            core_coordinates=coords("FONT-HEI", "RENDERER-XIN"),
            interaction_requirements={
                "native_font_code": "blocked",
                "shaper_sandbox": "enforced",
                "renderer_plugin_bridge": "enabled_for_verified_hei_tables",
                "clipboard_export": "blocked",
            },
            classifications=frozenset({"internal-draft"}),
            note="XIN 渲染器可以使用，但原生 hint 桥只允许已核验的黑体表。",
        ),
    ]
    return PolicyStore(standard_components(), combinations)
