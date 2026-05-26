"""Unit-тесты N2 — SecurityPolicy, TrunkPort, PolicyCompiler.

N2-01  SecurityPolicy entity (ordered rules)
N2-02  Policy compiler → nftables/iptables ruleset
N2-03  apply/verify lifecycle для SecurityPolicy
N2-04  Per-rule packet/byte counters
N2-05  Trunk Port (802.1q VLAN trunking)
"""

from __future__ import annotations

import pytest

from sdn_controller.adapters.memory import (
    InMemoryNodeRepository,
    InMemoryOutboxRepository,
    InMemorySecurityPolicyRepository,
    InMemoryServiceObjectRepository,
    InMemoryTrunkPortRepository,
)
from sdn_controller.core.entities.security_policy import SecurityPolicy, SecurityPolicyRule
from sdn_controller.core.entities.trunk_port import TrunkPort
from sdn_controller.core.services.event_publisher import EventPublisher
from sdn_controller.core.services.policy_compiler import PolicyCompiler
from sdn_controller.core.use_cases.n1 import CreateServiceObject, CreateServiceObjectCommand
from sdn_controller.core.use_cases.n2 import (
    AddPolicyRule,
    AddPolicyRuleCommand,
    ApplySecurityPolicy,
    CompileSecurityPolicy,
    CreateSecurityPolicy,
    CreateSecurityPolicyCommand,
    CreateTrunkPort,
    CreateTrunkPortCommand,
    DeleteSecurityPolicy,
    DeleteTrunkPort,
    GetSecurityPolicy,
    GetTrunkPort,
    ListSecurityPolicies,
    ListTrunkPorts,
    RemovePolicyRule,
    UpdateRuleCounters,
    UpdateCountersCommand,
    UpdateSecurityPolicy,
    UpdateSecurityPolicyCommand,
    UpdateTrunkPort,
    UpdateTrunkPortCommand,
)
from sdn_controller.core.value_objects.enums import SecurityPolicyStatus
from sdn_controller.core.value_objects.errors import NotFoundError, ValidationError
from sdn_controller.core.value_objects.ids import (
    NodeId,
    ProjectId,
    SecurityPolicyId,
    TrunkPortId,
)
from tests.conftest import CountingIdFactory, FrozenClock


# ---------------------------------------------------------------------------
# Вспомогательные фабрики
# ---------------------------------------------------------------------------


def _make_clock() -> FrozenClock:
    return FrozenClock()


def _make_ids() -> CountingIdFactory:
    return CountingIdFactory()


def _make_events(clock: FrozenClock, ids: CountingIdFactory) -> EventPublisher:
    outbox = InMemoryOutboxRepository()
    return EventPublisher(outbox=outbox, clock=clock, ids=ids)


def _make_policy(
    policy_id: str = "spol_1",
    name: str = "test-policy",
) -> SecurityPolicy:
    from datetime import UTC, datetime
    now = datetime(2026, 5, 26, 0, 0, 0, tzinfo=UTC)
    return SecurityPolicy(
        id=SecurityPolicyId(policy_id),
        name=name,
        created_at=now,
        updated_at=now,
    )


def _make_rule(priority: int = 100, action: str = "allow") -> SecurityPolicyRule:
    return SecurityPolicyRule.new(priority=priority, direction="ingress", action=action)


def _make_node(node_id: str = "node_1") -> "object":
    from datetime import UTC, datetime
    from sdn_controller.core.entities import Node
    now = datetime(2026, 5, 26, 0, 0, 0, tzinfo=UTC)
    return Node(
        id=NodeId(node_id),
        name="test-node",
        mgmt_ip="10.0.0.1",
        created_at=now,
        updated_at=now,
    )


# ---------------------------------------------------------------------------
# SecurityPolicyRule — валидация
# ---------------------------------------------------------------------------


class TestSecurityPolicyRule:
    def test_valid_rule(self) -> None:
        rule = SecurityPolicyRule.new(priority=100, direction="ingress", action="allow")
        assert rule.priority == 100
        assert rule.direction == "ingress"
        assert rule.action == "allow"
        assert rule.packet_count == 0
        assert rule.byte_count == 0

    def test_priority_bounds(self) -> None:
        SecurityPolicyRule.new(priority=1, direction="egress", action="deny")
        SecurityPolicyRule.new(priority=65535, direction="both", action="allow")

    def test_priority_out_of_range(self) -> None:
        with pytest.raises(ValidationError):
            SecurityPolicyRule.new(priority=0, direction="ingress", action="allow")
        with pytest.raises(ValidationError):
            SecurityPolicyRule.new(priority=65536, direction="ingress", action="allow")

    def test_invalid_direction(self) -> None:
        with pytest.raises(ValidationError):
            SecurityPolicyRule.new(priority=100, direction="forward", action="allow")

    def test_invalid_action(self) -> None:
        with pytest.raises(ValidationError):
            SecurityPolicyRule.new(priority=100, direction="ingress", action="reject")

    def test_invalid_source_type(self) -> None:
        with pytest.raises(ValidationError):
            SecurityPolicyRule(
                rule_id="r1",
                priority=100,
                direction="ingress",
                action="allow",
                source_type="unknown",
            )

    def test_valid_cidr_source(self) -> None:
        rule = SecurityPolicyRule(
            rule_id="r1",
            priority=100,
            direction="ingress",
            action="allow",
            source_type="cidr",
            source_value="10.0.0.0/8",
        )
        assert rule.source_type == "cidr"

    def test_invalid_cidr_raises(self) -> None:
        with pytest.raises(ValidationError):
            SecurityPolicyRule(
                rule_id="r1",
                priority=100,
                direction="ingress",
                action="allow",
                source_type="cidr",
                source_value="not-a-cidr",
            )

    def test_rule_id_auto_generated(self) -> None:
        r1 = SecurityPolicyRule.new(priority=100, direction="ingress", action="allow")
        r2 = SecurityPolicyRule.new(priority=100, direction="ingress", action="allow")
        assert r1.rule_id != r2.rule_id
        assert len(r1.rule_id) == 12


# ---------------------------------------------------------------------------
# SecurityPolicy entity
# ---------------------------------------------------------------------------


class TestSecurityPolicy:
    def test_add_rule_sorted_by_priority(self) -> None:
        clock = _make_clock()
        now = clock.now()
        policy = _make_policy()
        r1 = _make_rule(priority=200)
        r2 = _make_rule(priority=100)
        policy.add_rule(r1, now=now)
        policy.add_rule(r2, now=now)
        priorities = [r.priority for r in policy.rules]
        assert priorities == sorted(priorities)

    def test_add_rule_resets_to_draft(self) -> None:
        clock = _make_clock()
        policy = _make_policy()
        policy.status = SecurityPolicyStatus.COMPILED
        policy.compiled_ruleset = "some script"
        policy.add_rule(_make_rule(), now=clock.now())
        assert policy.status == SecurityPolicyStatus.DRAFT
        assert policy.compiled_ruleset is None

    def test_remove_rule(self) -> None:
        clock = _make_clock()
        policy = _make_policy()
        rule = _make_rule()
        policy.add_rule(rule, now=clock.now())
        assert len(policy.rules) == 1
        policy.remove_rule(rule.rule_id, now=clock.now())
        assert len(policy.rules) == 0

    def test_remove_nonexistent_rule_raises(self) -> None:
        policy = _make_policy()
        with pytest.raises(ValidationError):
            policy.remove_rule("nonexistent", now=_make_clock().now())

    def test_remove_rule_resets_to_draft(self) -> None:
        clock = _make_clock()
        policy = _make_policy()
        rule = _make_rule()
        policy.add_rule(rule, now=clock.now())
        policy.status = SecurityPolicyStatus.COMPILED
        policy.remove_rule(rule.rule_id, now=clock.now())
        assert policy.status == SecurityPolicyStatus.DRAFT

    def test_mark_compiled(self) -> None:
        clock = _make_clock()
        policy = _make_policy()
        policy.mark_compiled(ruleset="nft script here", now=clock.now())
        assert policy.status == SecurityPolicyStatus.COMPILED
        assert policy.compiled_ruleset == "nft script here"
        assert policy.compiled_at == clock.now()

    def test_mark_applied_requires_compiled(self) -> None:
        clock = _make_clock()
        policy = _make_policy()
        with pytest.raises(ValidationError):
            policy.mark_applied(now=clock.now())

    def test_mark_applied_from_compiled(self) -> None:
        clock = _make_clock()
        policy = _make_policy()
        policy.mark_compiled(ruleset="script", now=clock.now())
        policy.mark_applied(now=clock.now())
        assert policy.status == SecurityPolicyStatus.APPLIED
        assert policy.applied_at is not None

    def test_mark_failed(self) -> None:
        clock = _make_clock()
        policy = _make_policy()
        policy.mark_failed(now=clock.now())
        assert policy.status == SecurityPolicyStatus.FAILED

    def test_update_counters(self) -> None:
        clock = _make_clock()
        policy = _make_policy()
        rule = _make_rule()
        policy.add_rule(rule, now=clock.now())
        policy.update_counters(rule.rule_id, packet_count=1000, byte_count=65536)
        updated = policy.rules[0]
        assert updated.packet_count == 1000
        assert updated.byte_count == 65536

    def test_update_counters_nonexistent_rule(self) -> None:
        policy = _make_policy()
        with pytest.raises(ValidationError):
            policy.update_counters("no_such_rule", packet_count=0, byte_count=0)

    def test_update_metadata(self) -> None:
        clock = _make_clock()
        policy = _make_policy()
        policy.update(name="new-name", description="desc", labels={"env": "prod"}, now=clock.now())
        assert policy.name == "new-name"
        assert policy.description == "desc"
        assert policy.labels == {"env": "prod"}


# ---------------------------------------------------------------------------
# TrunkPort entity
# ---------------------------------------------------------------------------


class TestTrunkPort:
    def _make(
        self,
        vlan_ids: tuple[int, ...] = (10, 20, 30),
        native_vlan: int | None = None,
    ) -> TrunkPort:
        from datetime import UTC, datetime
        now = datetime(2026, 5, 26, 0, 0, 0, tzinfo=UTC)
        return TrunkPort(
            id=TrunkPortId("tport_1"),
            name="test-trunk",
            node_id=NodeId("node_1"),
            vlan_ids=vlan_ids,
            native_vlan=native_vlan,
            created_at=now,
            updated_at=now,
        )

    def test_vlan_ids_sorted_and_deduped(self) -> None:
        port = self._make(vlan_ids=(30, 10, 20, 10))
        assert port.vlan_ids == (10, 20, 30)

    def test_vlan_out_of_range_raises(self) -> None:
        with pytest.raises(ValidationError):
            self._make(vlan_ids=(0,))
        with pytest.raises(ValidationError):
            self._make(vlan_ids=(4095,))

    def test_native_vlan_must_be_in_vlan_ids(self) -> None:
        with pytest.raises(ValidationError):
            self._make(vlan_ids=(10, 20), native_vlan=30)

    def test_valid_native_vlan(self) -> None:
        port = self._make(vlan_ids=(10, 20, 30), native_vlan=10)
        assert port.native_vlan == 10

    def test_update_name(self) -> None:
        clock = _make_clock()
        port = self._make()
        port.update(name="new-trunk", now=clock.now())
        assert port.name == "new-trunk"

    def test_update_vlan_ids(self) -> None:
        clock = _make_clock()
        port = self._make(vlan_ids=(10, 20))
        port.update(vlan_ids=(100, 200, 300), now=clock.now())
        assert port.vlan_ids == (100, 200, 300)

    def test_update_native_vlan_not_in_new_vlan_ids_raises(self) -> None:
        clock = _make_clock()
        port = self._make(vlan_ids=(10, 20), native_vlan=10)
        with pytest.raises(ValidationError):
            port.update(vlan_ids=(100, 200), native_vlan=10, now=clock.now())

    def test_update_labels(self) -> None:
        clock = _make_clock()
        port = self._make()
        port.update(labels={"env": "test"}, now=clock.now())
        assert port.labels == {"env": "test"}


# ---------------------------------------------------------------------------
# PolicyCompiler (N2-02)
# ---------------------------------------------------------------------------


class TestPolicyCompiler:
    def _policy_with_rules(self) -> SecurityPolicy:
        clock = _make_clock()
        policy = _make_policy()
        r_allow = SecurityPolicyRule.new(
            priority=100, direction="ingress", action="allow",
            source_type="cidr", source_value="10.0.0.0/8",
        )
        r_deny = SecurityPolicyRule.new(
            priority=200, direction="egress", action="deny",
        )
        policy.add_rule(r_allow, now=clock.now())
        policy.add_rule(r_deny, now=clock.now())
        return policy

    def test_compile_produces_nft_script(self) -> None:
        compiler = PolicyCompiler()
        policy = self._policy_with_rules()
        script = compiler.compile(policy)
        assert "#!/usr/sbin/nft -f" in script
        assert "table inet" in script
        assert "chain input" in script
        assert "chain output" in script
        assert "counter" in script

    def test_compile_contains_policy_id(self) -> None:
        compiler = PolicyCompiler()
        policy = _make_policy()
        script = compiler.compile(policy)
        assert policy.id in script

    def test_compile_allow_action(self) -> None:
        compiler = PolicyCompiler()
        policy = _make_policy()
        rule = SecurityPolicyRule.new(
            priority=100, direction="ingress", action="allow",
            source_type="cidr", source_value="192.168.0.0/16",
        )
        policy.add_rule(rule, now=_make_clock().now())
        script = compiler.compile(policy)
        assert "accept" in script

    def test_compile_deny_action(self) -> None:
        compiler = PolicyCompiler()
        policy = _make_policy()
        rule = SecurityPolicyRule.new(priority=100, direction="ingress", action="deny")
        policy.add_rule(rule, now=_make_clock().now())
        script = compiler.compile(policy)
        assert "drop" in script

    def test_compile_unresolved_sg_skipped(self) -> None:
        compiler = PolicyCompiler()
        policy = _make_policy()
        rule = SecurityPolicyRule.new(
            priority=100, direction="ingress", action="allow",
            source_type="security_group", source_value="sg_xxx",
        )
        policy.add_rule(rule, now=_make_clock().now())
        script = compiler.compile(policy, resolved_cidrs={})
        # Правило должно быть пропущено с комментарием
        assert "ПРОПУЩЕНО" in script

    def test_compile_resolved_sg(self) -> None:
        compiler = PolicyCompiler()
        policy = _make_policy()
        rule = SecurityPolicyRule.new(
            priority=100, direction="ingress", action="allow",
            source_type="security_group", source_value="sg_xxx",
        )
        policy.add_rule(rule, now=_make_clock().now())
        script = compiler.compile(
            policy,
            resolved_cidrs={"security_group:sg_xxx": ["10.1.0.0/24"]},
        )
        assert "10.1.0.0/24" in script

    def test_compile_disabled_rule_skipped(self) -> None:
        compiler = PolicyCompiler()
        policy = _make_policy()
        rule = SecurityPolicyRule.new(
            priority=100, direction="ingress", action="allow", enabled=False
        )
        policy.add_rule(rule, now=_make_clock().now())
        script = compiler.compile(policy)
        assert rule.rule_id not in script

    def test_compile_both_direction_appears_in_both_chains(self) -> None:
        compiler = PolicyCompiler()
        policy = _make_policy()
        rule = SecurityPolicyRule.new(priority=100, direction="both", action="allow")
        policy.add_rule(rule, now=_make_clock().now())
        script = compiler.compile(policy)
        # rule_id должен упоминаться дважды (input и output)
        assert script.count(rule.rule_id) == 2


# ---------------------------------------------------------------------------
# SecurityPolicy use cases
# ---------------------------------------------------------------------------


class TestCreateSecurityPolicy:
    @pytest.mark.asyncio
    async def test_creates_policy_in_draft(self) -> None:
        clock = _make_clock()
        ids = _make_ids()
        policies = InMemorySecurityPolicyRepository()
        events = _make_events(clock, ids)
        uc = CreateSecurityPolicy(policies=policies, clock=clock, ids=ids, events=events)
        policy = await uc.execute(CreateSecurityPolicyCommand(name="my-policy"))
        assert policy.name == "my-policy"
        assert policy.status == SecurityPolicyStatus.DRAFT
        assert policy.id == "spol_1"
        stored = await policies.get(policy.id)
        assert stored is not None

    @pytest.mark.asyncio
    async def test_emits_created_event(self) -> None:
        clock = _make_clock()
        ids = _make_ids()
        outbox = InMemoryOutboxRepository()
        events = EventPublisher(outbox=outbox, clock=clock, ids=ids)
        policies = InMemorySecurityPolicyRepository()
        uc = CreateSecurityPolicy(policies=policies, clock=clock, ids=ids, events=events)
        await uc.execute(CreateSecurityPolicyCommand(name="p"))
        pending = await outbox.list_undelivered(limit=10)
        assert any(e.event_type == "security_policy.created" for e in pending)


class TestGetSecurityPolicy:
    @pytest.mark.asyncio
    async def test_get_existing(self) -> None:
        clock = _make_clock()
        ids = _make_ids()
        policies = InMemorySecurityPolicyRepository()
        events = _make_events(clock, ids)
        create_uc = CreateSecurityPolicy(policies=policies, clock=clock, ids=ids, events=events)
        created = await create_uc.execute(CreateSecurityPolicyCommand(name="p"))
        get_uc = GetSecurityPolicy(policies=policies)
        fetched = await get_uc.execute(created.id)
        assert fetched.id == created.id

    @pytest.mark.asyncio
    async def test_get_missing_raises(self) -> None:
        policies = InMemorySecurityPolicyRepository()
        uc = GetSecurityPolicy(policies=policies)
        with pytest.raises(NotFoundError):
            await uc.execute(SecurityPolicyId("spol_missing"))


class TestAddPolicyRule:
    @pytest.mark.asyncio
    async def test_add_rule_to_policy(self) -> None:
        clock = _make_clock()
        ids = _make_ids()
        policies = InMemorySecurityPolicyRepository()
        events = _make_events(clock, ids)
        create_uc = CreateSecurityPolicy(policies=policies, clock=clock, ids=ids, events=events)
        policy = await create_uc.execute(CreateSecurityPolicyCommand(name="p"))

        add_uc = AddPolicyRule(policies=policies, clock=clock, events=events)
        updated = await add_uc.execute(
            AddPolicyRuleCommand(
                policy_id=policy.id,
                priority=100,
                direction="ingress",
                action="allow",
            )
        )
        assert len(updated.rules) == 1
        assert updated.rules[0].priority == 100

    @pytest.mark.asyncio
    async def test_add_rule_to_missing_policy_raises(self) -> None:
        clock = _make_clock()
        ids = _make_ids()
        policies = InMemorySecurityPolicyRepository()
        events = _make_events(clock, ids)
        uc = AddPolicyRule(policies=policies, clock=clock, events=events)
        with pytest.raises(NotFoundError):
            await uc.execute(
                AddPolicyRuleCommand(
                    policy_id=SecurityPolicyId("spol_x"),
                    priority=100,
                    direction="ingress",
                    action="allow",
                )
            )


class TestRemovePolicyRule:
    @pytest.mark.asyncio
    async def test_remove_existing_rule(self) -> None:
        clock = _make_clock()
        ids = _make_ids()
        policies = InMemorySecurityPolicyRepository()
        events = _make_events(clock, ids)
        create_uc = CreateSecurityPolicy(policies=policies, clock=clock, ids=ids, events=events)
        policy = await create_uc.execute(CreateSecurityPolicyCommand(name="p"))

        add_uc = AddPolicyRule(policies=policies, clock=clock, events=events)
        policy = await add_uc.execute(
            AddPolicyRuleCommand(
                policy_id=policy.id, priority=100, direction="ingress", action="allow"
            )
        )
        rule_id = policy.rules[0].rule_id

        rm_uc = RemovePolicyRule(policies=policies, clock=clock, events=events)
        updated = await rm_uc.execute(policy.id, rule_id)
        assert len(updated.rules) == 0


class TestCompileSecurityPolicy:
    @pytest.mark.asyncio
    async def test_compile_changes_status(self) -> None:
        clock = _make_clock()
        ids = _make_ids()
        policies = InMemorySecurityPolicyRepository()
        service_objects = InMemoryServiceObjectRepository()
        events = _make_events(clock, ids)
        create_uc = CreateSecurityPolicy(policies=policies, clock=clock, ids=ids, events=events)
        policy = await create_uc.execute(CreateSecurityPolicyCommand(name="p"))

        compile_uc = CompileSecurityPolicy(
            policies=policies,
            service_objects=service_objects,
            clock=clock,
            events=events,
        )
        compiled = await compile_uc.execute(policy.id)
        assert compiled.status == SecurityPolicyStatus.COMPILED
        assert compiled.compiled_ruleset is not None
        assert "nft" in compiled.compiled_ruleset


class TestApplySecurityPolicy:
    @pytest.mark.asyncio
    async def test_apply_compiled_policy(self) -> None:
        clock = _make_clock()
        ids = _make_ids()
        policies = InMemorySecurityPolicyRepository()
        service_objects = InMemoryServiceObjectRepository()
        events = _make_events(clock, ids)
        create_uc = CreateSecurityPolicy(policies=policies, clock=clock, ids=ids, events=events)
        policy = await create_uc.execute(CreateSecurityPolicyCommand(name="p"))
        compile_uc = CompileSecurityPolicy(
            policies=policies, service_objects=service_objects, clock=clock, events=events
        )
        await compile_uc.execute(policy.id)
        apply_uc = ApplySecurityPolicy(policies=policies, clock=clock, events=events)
        applied = await apply_uc.execute(policy.id)
        assert applied.status == SecurityPolicyStatus.APPLIED
        assert applied.applied_at is not None

    @pytest.mark.asyncio
    async def test_apply_draft_policy_raises(self) -> None:
        clock = _make_clock()
        ids = _make_ids()
        policies = InMemorySecurityPolicyRepository()
        events = _make_events(clock, ids)
        create_uc = CreateSecurityPolicy(policies=policies, clock=clock, ids=ids, events=events)
        policy = await create_uc.execute(CreateSecurityPolicyCommand(name="p"))
        apply_uc = ApplySecurityPolicy(policies=policies, clock=clock, events=events)
        with pytest.raises(ValidationError):
            await apply_uc.execute(policy.id)


class TestUpdateRuleCounters:
    @pytest.mark.asyncio
    async def test_update_counters(self) -> None:
        clock = _make_clock()
        ids = _make_ids()
        policies = InMemorySecurityPolicyRepository()
        events = _make_events(clock, ids)
        create_uc = CreateSecurityPolicy(policies=policies, clock=clock, ids=ids, events=events)
        policy = await create_uc.execute(CreateSecurityPolicyCommand(name="p"))
        add_uc = AddPolicyRule(policies=policies, clock=clock, events=events)
        policy = await add_uc.execute(
            AddPolicyRuleCommand(
                policy_id=policy.id, priority=100, direction="ingress", action="allow"
            )
        )
        rule_id = policy.rules[0].rule_id

        counter_uc = UpdateRuleCounters(policies=policies, clock=clock)
        result = await counter_uc.execute(
            UpdateCountersCommand(
                policy_id=policy.id,
                rule_id=rule_id,
                packet_count=5000,
                byte_count=1024000,
            )
        )
        updated_rule = next(r for r in result.rules if r.rule_id == rule_id)
        assert updated_rule.packet_count == 5000
        assert updated_rule.byte_count == 1024000


class TestDeleteSecurityPolicy:
    @pytest.mark.asyncio
    async def test_delete_existing(self) -> None:
        clock = _make_clock()
        ids = _make_ids()
        policies = InMemorySecurityPolicyRepository()
        events = _make_events(clock, ids)
        create_uc = CreateSecurityPolicy(policies=policies, clock=clock, ids=ids, events=events)
        policy = await create_uc.execute(CreateSecurityPolicyCommand(name="p"))
        del_uc = DeleteSecurityPolicy(policies=policies, events=events)
        await del_uc.execute(policy.id)
        assert await policies.get(policy.id) is None

    @pytest.mark.asyncio
    async def test_delete_missing_raises(self) -> None:
        policies = InMemorySecurityPolicyRepository()
        outbox = InMemoryOutboxRepository()
        clock = _make_clock()
        ids = _make_ids()
        events = EventPublisher(outbox=outbox, clock=clock, ids=ids)
        uc = DeleteSecurityPolicy(policies=policies, events=events)
        with pytest.raises(NotFoundError):
            await uc.execute(SecurityPolicyId("spol_x"))


class TestUpdateSecurityPolicy:
    @pytest.mark.asyncio
    async def test_update_name(self) -> None:
        clock = _make_clock()
        ids = _make_ids()
        policies = InMemorySecurityPolicyRepository()
        events = _make_events(clock, ids)
        create_uc = CreateSecurityPolicy(policies=policies, clock=clock, ids=ids, events=events)
        policy = await create_uc.execute(CreateSecurityPolicyCommand(name="old"))
        upd_uc = UpdateSecurityPolicy(policies=policies, clock=clock, events=events)
        updated = await upd_uc.execute(
            UpdateSecurityPolicyCommand(policy_id=policy.id, name="new-name")
        )
        assert updated.name == "new-name"


class TestListSecurityPolicies:
    @pytest.mark.asyncio
    async def test_list_empty(self) -> None:
        policies = InMemorySecurityPolicyRepository()
        uc = ListSecurityPolicies(policies=policies)
        result = await uc.execute()
        assert result == []

    @pytest.mark.asyncio
    async def test_list_returns_all(self) -> None:
        clock = _make_clock()
        ids = _make_ids()
        policies = InMemorySecurityPolicyRepository()
        events = _make_events(clock, ids)
        create_uc = CreateSecurityPolicy(policies=policies, clock=clock, ids=ids, events=events)
        await create_uc.execute(CreateSecurityPolicyCommand(name="p1"))
        await create_uc.execute(CreateSecurityPolicyCommand(name="p2"))
        uc = ListSecurityPolicies(policies=policies)
        result = await uc.execute()
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_list_filter_by_project(self) -> None:
        clock = _make_clock()
        ids = _make_ids()
        policies = InMemorySecurityPolicyRepository()
        events = _make_events(clock, ids)
        create_uc = CreateSecurityPolicy(policies=policies, clock=clock, ids=ids, events=events)
        await create_uc.execute(
            CreateSecurityPolicyCommand(name="p1", project_id=ProjectId("proj_1"))
        )
        await create_uc.execute(CreateSecurityPolicyCommand(name="p2"))
        uc = ListSecurityPolicies(policies=policies)
        result = await uc.execute(project_id=ProjectId("proj_1"))
        assert len(result) == 1
        assert result[0].name == "p1"


# ---------------------------------------------------------------------------
# TrunkPort use cases (N2-05)
# ---------------------------------------------------------------------------


class TestCreateTrunkPort:
    @pytest.mark.asyncio
    async def test_creates_trunk_port(self) -> None:
        clock = _make_clock()
        ids = _make_ids()
        nodes = InMemoryNodeRepository()
        trunks = InMemoryTrunkPortRepository()
        events = _make_events(clock, ids)
        node = _make_node()
        await nodes.save(node)

        uc = CreateTrunkPort(trunks=trunks, nodes=nodes, clock=clock, ids=ids, events=events)
        port = await uc.execute(
            CreateTrunkPortCommand(
                name="trunk-0",
                node_id=node.id,
                vlan_ids=[10, 20, 30],
            )
        )
        assert port.name == "trunk-0"
        assert port.vlan_ids == (10, 20, 30)
        assert port.id == "tport_1"

    @pytest.mark.asyncio
    async def test_node_not_found_raises(self) -> None:
        clock = _make_clock()
        ids = _make_ids()
        nodes = InMemoryNodeRepository()
        trunks = InMemoryTrunkPortRepository()
        events = _make_events(clock, ids)
        uc = CreateTrunkPort(trunks=trunks, nodes=nodes, clock=clock, ids=ids, events=events)
        with pytest.raises(NotFoundError):
            await uc.execute(
                CreateTrunkPortCommand(
                    name="t",
                    node_id=NodeId("no_such_node"),
                    vlan_ids=[10],
                )
            )

    @pytest.mark.asyncio
    async def test_emits_created_event(self) -> None:
        clock = _make_clock()
        ids = _make_ids()
        nodes = InMemoryNodeRepository()
        trunks = InMemoryTrunkPortRepository()
        outbox = InMemoryOutboxRepository()
        events = EventPublisher(outbox=outbox, clock=clock, ids=ids)
        node = _make_node()
        await nodes.save(node)
        uc = CreateTrunkPort(trunks=trunks, nodes=nodes, clock=clock, ids=ids, events=events)
        await uc.execute(
            CreateTrunkPortCommand(name="t", node_id=node.id, vlan_ids=[100])
        )
        pending = await outbox.list_undelivered(limit=10)
        assert any(e.event_type == "trunk_port.created" for e in pending)


class TestGetTrunkPort:
    @pytest.mark.asyncio
    async def test_get_missing_raises(self) -> None:
        trunks = InMemoryTrunkPortRepository()
        uc = GetTrunkPort(trunks=trunks)
        with pytest.raises(NotFoundError):
            await uc.execute(TrunkPortId("tport_x"))


class TestUpdateTrunkPort:
    @pytest.mark.asyncio
    async def test_update_name_and_vlans(self) -> None:
        clock = _make_clock()
        ids = _make_ids()
        nodes = InMemoryNodeRepository()
        trunks = InMemoryTrunkPortRepository()
        events = _make_events(clock, ids)
        node = _make_node()
        await nodes.save(node)
        create_uc = CreateTrunkPort(trunks=trunks, nodes=nodes, clock=clock, ids=ids, events=events)
        port = await create_uc.execute(
            CreateTrunkPortCommand(name="t", node_id=node.id, vlan_ids=[10, 20])
        )
        upd_uc = UpdateTrunkPort(trunks=trunks, clock=clock, events=events)
        updated = await upd_uc.execute(
            UpdateTrunkPortCommand(
                port_id=port.id,
                name="renamed",
                vlan_ids=[100, 200],
            )
        )
        assert updated.name == "renamed"
        assert updated.vlan_ids == (100, 200)


class TestDeleteTrunkPort:
    @pytest.mark.asyncio
    async def test_delete_existing(self) -> None:
        clock = _make_clock()
        ids = _make_ids()
        nodes = InMemoryNodeRepository()
        trunks = InMemoryTrunkPortRepository()
        events = _make_events(clock, ids)
        node = _make_node()
        await nodes.save(node)
        create_uc = CreateTrunkPort(trunks=trunks, nodes=nodes, clock=clock, ids=ids, events=events)
        port = await create_uc.execute(
            CreateTrunkPortCommand(name="t", node_id=node.id, vlan_ids=[10])
        )
        del_uc = DeleteTrunkPort(trunks=trunks, events=events)
        await del_uc.execute(port.id)
        assert await trunks.get(port.id) is None

    @pytest.mark.asyncio
    async def test_delete_missing_raises(self) -> None:
        trunks = InMemoryTrunkPortRepository()
        outbox = InMemoryOutboxRepository()
        clock = _make_clock()
        ids = _make_ids()
        events = EventPublisher(outbox=outbox, clock=clock, ids=ids)
        uc = DeleteTrunkPort(trunks=trunks, events=events)
        with pytest.raises(NotFoundError):
            await uc.execute(TrunkPortId("tport_x"))


class TestListTrunkPorts:
    @pytest.mark.asyncio
    async def test_list_filter_by_node(self) -> None:
        clock = _make_clock()
        ids = _make_ids()
        nodes = InMemoryNodeRepository()
        trunks = InMemoryTrunkPortRepository()
        events = _make_events(clock, ids)
        node1 = _make_node("node_1")
        node2 = _make_node("node_2")
        await nodes.save(node1)
        await nodes.save(node2)
        create_uc = CreateTrunkPort(trunks=trunks, nodes=nodes, clock=clock, ids=ids, events=events)
        await create_uc.execute(CreateTrunkPortCommand(name="t1", node_id=node1.id, vlan_ids=[10]))
        await create_uc.execute(CreateTrunkPortCommand(name="t2", node_id=node2.id, vlan_ids=[20]))
        list_uc = ListTrunkPorts(trunks=trunks)
        result = await list_uc.execute(node_id=node1.id)
        assert len(result) == 1
        assert result[0].name == "t1"
