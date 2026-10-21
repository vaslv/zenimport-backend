"""Frozen public migration schema, generated without starting an application."""
from backend import portable_api as api, migration_participant as participant


def specification():
    models=[api.Receipt,api.Task,api.Operation,api.ParticipantStatus,api.Capabilities,api.Identity,api.Status,
        api.CommandResult,api.ChunkRequest,api.ChunkReceipt,api.FinalizeRequest,api.QuiesceRequest,api.QuiesceResult,api.Empty]
    return dict(contract_version=1,protocol_version='2',ordinary_api_version=1,
        prefix='/api/v1/migration/portable',integer_wire_policy='canonical decimal strings; URL chunk indexes are integers',
        peer_receipts='integrity only; coordinator verifies authenticated participant status',
        receipt_nulls='all receipt keys, including nulls, are preserved in HTTP replies and hash preimages',
        routes=[['GET','/capabilities'],['POST','/identity'],['POST','/quiesce'],['GET','/switches/{switch}'],['POST','/commands/{command}'],
            ['PUT','/switches/{switch}/chunks/{number}'],['GET','/switches/{switch}/chunks/{number}'],
            ['POST','/finalize'],['POST','/work/{task}/retry']],
        commands={name:dict(required=sorted(participant.COMMON|fields),additional_properties=False)
            for name,fields in participant.FIELDS.items() if name!='source_seal'},
        schemas={model.__name__:model.model_json_schema() for model in models})
