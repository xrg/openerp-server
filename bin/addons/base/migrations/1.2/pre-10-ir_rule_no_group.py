# -*- coding: utf8 -*-

__name__ = "ir.rule: obsolete ir.rule.groups"

def migrate(cr, version):
    if column_exists(cr, 'ir_rule', 'name'):
        return

    cr.execute("ALTER TABLE ir_rule ADD name VARCHAR(128), "
        " ADD model_id INTEGER REFERENCES ir_model(id), "
        " ADD global BOOLEAN ");

    # drop the cascade... 
    cr.execute("ALTER TABLE ir_rule ALTER rule_group DROP NOT NULL, "
        " DROP CONSTRAINT ir_rule_rule_group_fkey")
    
    cr.execute('''CREATE TABLE rule_group_rel 
         ( rule_group_id INTEGER REFERENCES ir_rule(id) NOT NULL, 
         group_id INTEGER REFERENCES res_groups(id) NOT NULL); ''')

    cr.execute('''INSERT INTO rule_group_rel(rule_group_id, group_id)
        SELECT ir_rule.id, ggr.group_id 
            FROM ir_rule, ir_rule_group, group_rule_group_rel ggr
            WHERE ggr.rule_group_id = ir_rule_group.id
              AND ir_rule.rule_group = ir_rule_group.id; ''')

    cr.execute('''UPDATE ir_rule
        SET name = ir_rule_group.name,
            model_id = ir_rule_group.model_id,
            rule_group = NULL,
            global = NOT EXISTS ( SELECT 1 FROM group_rule_group_rel ggr WHERE ggr.rule_group_id = ir_rule_group.id )
        FROM ir_rule_group
        WHERE ir_rule.rule_group = ir_rule_group.id AND ir_rule.model_id IS NULL; ''' )
        
    cr.execute(''' UPDATE ir_rule 
        SET domain_force = E'[(\'' || ir_model_fields.name || E'\',\'' ||
                ir_rule.operator || E'\','|| ir_rule.operand || E')]'
        FROM ir_model_fields
        WHERE domain_force IS NULL AND ir_rule.field_id = ir_model_fields.id; ''')
    cr.execute("ALTER TABLE ir_rule ALTER model_id SET NOT NULL, ALTER global SET NOT NULL")
    cr.commit()

def column_exists(cr, table, column):
    cr.execute("SELECT count(1)"
               "  FROM pg_class c, pg_attribute a"
               " WHERE c.relname=%s"
               "   AND c.oid=a.attrelid"
               "   AND a.attname=%s",
               (table, column))
    return cr.fetchone()[0] != 0

def rename_column(cr, table, old, new):
    if column_exists(cr, table, old) and not column_exists(cr, table, new):
        cr.execute('ALTER TABLE "%s" RENAME COLUMN "%s" TO "%s"' % (table, old, new))

