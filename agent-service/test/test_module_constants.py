from app.system.module_constants import (
    ModuleConstants,
    normalize_module_id,
    resolve_record_module,
)


def test_record_module_ids_are_resolved_case_insensitively():
    assert normalize_module_id("78c873aa-bdf4-4f1a-9f63-6a8fbb788b95") == ModuleConstants.Tasks.lower()
    resolved = resolve_record_module("78c873aa-bdf4-4f1a-9f63-6a8fbb788b95")
    assert resolved == {
        "idRecordModule": ModuleConstants.Tasks.lower(),
        "moduleName": "Tasks",
        "known": "true",
    }


def test_unknown_or_invalid_record_module_is_preserved_as_unknown():
    assert resolve_record_module("not-a-module") == {
        "idRecordModule": "",
        "moduleName": "",
        "known": "false",
    }
