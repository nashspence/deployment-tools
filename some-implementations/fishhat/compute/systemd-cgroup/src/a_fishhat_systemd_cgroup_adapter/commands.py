"""Native systemd setting projection; placement still requires native qualification."""

from typing import Any

from fishhat_contracts.validation import ContractError, validate


def require(ok: bool, code: str) -> None:
    if not ok:
        raise ContractError(code)


def systemd_compute_settings(compute: dict[str, Any]) -> dict[str, str]:
    """Render ancestor slice controls. Native placement and support are not tested."""
    validate("resources.schema.json", compute, "ComputeEnvelope")
    require(compute["memory_high_bytes"] <= compute["memory_max_bytes"], "memory high exceeds max")
    whole, tenth = divmod(compute["cpu_millis"], 10)
    quota = str(whole) + ("." + str(tenth) if tenth else "") + "%"
    settings = {
        "CPUAccounting": "yes",
        "MemoryAccounting": "yes",
        "TasksAccounting": "yes",
        "CPUQuota": quota,
        "CPUWeight": str(compute["cpu_weight"]),
        "MemoryHigh": str(compute["memory_high_bytes"]),
        "MemoryMax": str(compute["memory_max_bytes"]),
        "MemorySwapMax": str(compute["swap_max_bytes"]),
        "TasksMax": str(compute["tasks_max"]),
    }
    if compute["io_weight"] is not None:
        settings["IOAccounting"] = "yes"
        settings["IOWeight"] = str(compute["io_weight"])
    # IO device handles and tmpfs mounts need their separate native host adapter.
    return settings
