--- Emulate old behavior where ir.model.data records were immediately deleted

CREATE OR REPLACE RULE ir_model_data_delete AS ON UPDATE TO ir_model_data
    WHERE NEW.res_id = 0 AND OLD.source IN ('orm', 'xml')
    DO INSTEAD DELETE FROM ir_model_data WHERE id = OLD.id;

-- eof
