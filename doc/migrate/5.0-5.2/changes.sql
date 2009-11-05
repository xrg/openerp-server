
ALTER TABLE res_users ADD company INTEGER;

ALTER TABLE ir_model_fields ADD selectable boolean NOT NULL DEFAULT TRUE;

-- The name of the pointer changed, we need to update it so that the
-- old group is reused.
UPDATE ir_model_data
    SET name = 'res_groups_openofficereportdesigner0' 
  WHERE name = 'res_groups_openofficereportdesinger0' 
    AND module = 'base_report_designer';
