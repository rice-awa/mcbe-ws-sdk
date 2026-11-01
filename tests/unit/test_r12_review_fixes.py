from mcbe_ws_sdk import CapabilityContext


def test_capability_context_remains_hashable_with_mapping_metadata() -> None:
    default = CapabilityContext()
    assert {default: "default"}[CapabilityContext()] == "default"

    first = CapabilityContext(metadata={"extra": {"items": [1, 2]}})
    equal = CapabilityContext(metadata={"extra": {"items": [1, 2]}})
    different = CapabilityContext(metadata={"extra": {"items": [2, 1]}})
    assert first == equal
    assert first != different
    assert hash(first) == hash(equal)
    assert {first: "value"}[equal] == "value"
