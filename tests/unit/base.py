def create_db_relation(harness, app_name, unit_name, db_rel_request):
    rel_name = 'db'
    relation_id = harness.add_relation(rel_name, app_name)
    harness.add_relation_unit(relation_id, unit_name)
    harness.update_relation_data(relation_id, unit_name, db_rel_request)
    return harness.model.get_relation(rel_name, relation_id)
