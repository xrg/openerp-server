-------------------------------------------------------------------------
-- Pure SQL
-------------------------------------------------------------------------

-- This file bootstraps the necessary SQL tables for OpenERP, in order to
-- run the rest of the ORM create procedure

-------------------------------------------------------------------------
-- IR dictionary
-------------------------------------------------------------------------

CREATE TABLE ir_values
(
    id serial PRIMARY KEY,
    name varchar(128) NOT NULL,
    key varchar(128) NOT NULL,
    key2 varchar(128) NOT NULL,
    model varchar(128) NOT NULL,
    "value" text,
    meta text DEFAULT NULL,
    res_id integer DEFAULT NULL
) WITHOUT OIDS;

-------------------------------------------------------------------------
-- Modules Description
-------------------------------------------------------------------------

CREATE TABLE ir_model (
  id serial PRIMARY KEY,
  model varchar(64) DEFAULT ''::varchar NOT NULL,
  name varchar(64),
  state varchar(16),
  info text
) WITHOUT OIDS;

CREATE TABLE ir_model_fields (
  id serial PRIMARY KEY,
  model varchar(64) DEFAULT ''::varchar NOT NULL,
  model_id int REFERENCES ir_model ON DELETE CASCADE,
  name varchar(64) DEFAULT ''::varchar NOT NULL,
  relation varchar(64),
  select_level varchar(16),
  field_description varchar(256),
  ttype varchar(64),
  state varchar(16) DEFAULT 'base',
  view_load boolean,
  relate boolean DEFAULT False,
  relation_field varchar(64),
  "translate" boolean NOT NULL DEFAULT False,
  selectable boolean NOT NULL DEFAULT False,
  readonly boolean NOT NULL DEFAULT False,
  required boolean NOT NULL DEFAULT False
) WITHOUT OIDS;


-------------------------------------------------------------------------
-- Actions
-------------------------------------------------------------------------

CREATE TABLE ir_actions (
    id serial NOT NULL PRIMARY KEY,
    create_date TIMESTAMP DEFAULT now(),
    create_uid INTEGER,
    write_date TIMESTAMP,
    write_uid INTEGER,
    name varchar(64) DEFAULT ''::varchar NOT NULL,
    "type" varchar(32) DEFAULT 'window'::varchar NOT NULL,
    "usage" varchar(32) DEFAULT NULL
) WITHOUT OIDS;

CREATE TABLE ir_act_window (
    view_id integer,
    res_model varchar(64),
    view_type varchar(16),
    "domain" varchar(250),
    PRIMARY KEY(id)
)
INHERITS (ir_actions);

CREATE TABLE ir_act_report_xml (
    model varchar(64) NOT NULL,
    report_name varchar(64) NOT NULL,
    report_xsl varchar(256),
    report_xml varchar(256),
    auto boolean DEFAULT true,
    PRIMARY KEY(id)
)
INHERITS (ir_actions);

CREATE TABLE ir_act_report_custom (
    report_id int,
--  report_id int REFERENCES ir_report_custom
    PRIMARY KEY(id)
)
INHERITS (ir_actions);

CREATE TABLE ir_act_wizard (
    wiz_name varchar(64) NOT NULL,
    PRIMARY KEY(id)
)
INHERITS (ir_actions);

CREATE TABLE ir_act_url (
    url text NOT NULL,
    target varchar(16) NOT NULL,
    PRIMARY KEY(id)
)
INHERITS (ir_actions);

CREATE TABLE ir_act_server (
    PRIMARY KEY(id)
)
INHERITS (ir_actions);


CREATE TABLE ir_ui_view (
    id serial NOT NULL,
    name varchar(64) DEFAULT ''::varchar NOT NULL,
    model varchar(64) DEFAULT ''::varchar NOT NULL,
    "type" varchar(16) DEFAULT 'form'::varchar NOT NULL,
    arch text NOT NULL,
    field_parent varchar(64),
    priority integer DEFAULT 5 NOT NULL,
    PRIMARY KEY(id)
) WITHOUT OIDS;

CREATE INDEX ir_ui_view_model_type_idx ON ir_ui_view(model,"type");

CREATE TABLE ir_ui_menu (
    id serial NOT NULL,
    parent_id int REFERENCES ir_ui_menu ON DELETE SET NULL,
    name varchar(64) DEFAULT ''::varchar NOT NULL,
    icon varchar(64) DEFAULT ''::varchar,
    PRIMARY KEY (id)
) WITHOUT OIDS;

select setval('ir_ui_menu_id_seq', 2);

---------------------------------
-- Res users
---------------------------------

-- level:
--   0  RESTRICT TO USER
--   1  RESTRICT TO GROUP
--   2  PUBLIC

CREATE TABLE res_users (
    id serial NOT NULL,
    name varchar(64) NOT NULL,
    active boolean DEFAULT True,
    login varchar(64) NOT NULL UNIQUE,
    "password" varchar(64) DEFAULT NULL,
    email varchar(64) DEFAULT NULL,
    context_tz varchar(64) DEFAULT NULL,
    signature text,
    context_lang varchar(16) DEFAULT '',
    -- No FK REFERENCES below, will be added later by ORM
    -- (when the destination rows exist)
    company_id int,
    PRIMARY KEY(id)
) WITHOUT OIDS;

CREATE TABLE res_groups (
    id serial NOT NULL,
    name varchar(64) NOT NULL,
    PRIMARY KEY(id)
) WITHOUT OIDS;

CREATE TABLE res_groups_users_rel (
    uid integer NOT NULL REFERENCES res_users ON DELETE CASCADE,
    gid integer NOT NULL REFERENCES res_groups ON DELETE CASCADE,
    UNIQUE("uid","gid")
) WITHOUT OIDS;

CREATE INDEX res_groups_users_rel_uid_idx on res_groups_users_rel (uid);
CREATE INDEX res_groups_users_rel_gid_idx on res_groups_users_rel (gid);


---------------------------------
-- Workflows
---------------------------------

CREATE TABLE wkf
(
    id serial,
    name varchar(64),
    osv varchar(64),
    on_create bool DEFAULT False,
    PRIMARY KEY(id)
) WITHOUT OIDS;

CREATE TABLE wkf_activity
(
    id serial PRIMARY KEY,
    wkf_id int REFERENCES wkf ON DELETE CASCADE,
    subflow_id int REFERENCES wkf ON DELETE SET NULL,
    split_mode varchar(3) DEFAULT 'XOR',
    join_mode varchar(3) DEFAULT 'XOR',
    kind varchar(64) NOT NULL DEFAULT 'dummy',
    name varchar(64),
    signal_send varchar(32) DEFAULT NULL,
    flow_start boolean DEFAULT False,
    flow_stop boolean DEFAULT False,
    action text DEFAULT NULL
) WITHOUT OIDS;

CREATE TABLE wkf_transition
(
    id serial PRIMARY KEY,
    act_from int REFERENCES wkf_activity ON DELETE CASCADE,
    act_to int REFERENCES wkf_activity ON DELETE CASCADE,
    condition varchar(128),

    trigger_type varchar(128),
    trigger_expr_id varchar(128),

    signal varchar(64) DEFAULT NULL,
    group_id int REFERENCES res_groups ON DELETE SET NULL
) WITHOUT OIDS;

CREATE TABLE wkf_instance
(
    id serial PRIMARY KEY,
    wkf_id int REFERENCES wkf ON DELETE RESTRICT,
    uid int DEFAULT NULL,
    res_id int NOT NULL,
    res_type varchar(64) NOT NULL,
    state varchar(32) NOT NULL DEFAULT 'active'
) WITHOUT OIDS;

CREATE TABLE wkf_workitem
(
    id serial PRIMARY KEY,
    act_id int NOT NULL REFERENCES wkf_activity ON DELETE CASCADE,
    inst_id int NOT NULL REFERENCES wkf_instance ON DELETE CASCADE,
    subflow_id int REFERENCES wkf_instance ON DELETE CASCADE,
    state varchar(64) DEFAULT 'blocked'
) WITHOUT OIDS;

CREATE TABLE wkf_witm_trans
(
    trans_id int NOT NULL REFERENCES wkf_transition ON DELETE CASCADE,
    inst_id int NOT NULL REFERENCES wkf_instance ON DELETE CASCADE
) WITHOUT OIDS;

CREATE INDEX wkf_witm_trans_inst_idx on wkf_witm_trans (inst_id);

CREATE TABLE wkf_logs
(
    id serial,
    res_type varchar(128) NOT NULL,
    res_id int NOT NULL,
    uid int REFERENCES res_users ON DELETE SET NULL,
    act_id int REFERENCES wkf_activity ON DELETE SET NULL,
    time time NOT NULL,
    info varchar(128) DEFAULT NULL,
    PRIMARY KEY(id)
) WITHOUT OIDS;

---------------------------------
-- Modules
---------------------------------

CREATE TABLE ir_module_category (
    id serial NOT NULL,
    create_uid integer REFERENCES res_users ON DELETE SET NULL,
    create_date timestamp WITHOUT time zone,
    write_date timestamp WITHOUT time zone,
    write_uid integer REFERENCES res_users ON DELETE SET NULL,
    parent_id integer REFERENCES ir_module_category ON DELETE SET NULL,
    name character varying(128) NOT NULL,
    PRIMARY KEY(id)
) WITHOUT OIDS;


CREATE TABLE ir_module_module (
    id serial NOT NULL,
    create_uid integer REFERENCES res_users ON DELETE SET NULL,
    create_date timestamp WITHOUT time zone,
    write_date timestamp WITHOUT time zone,
    write_uid integer REFERENCES res_users ON DELETE SET NULL,
    website character varying(256),
    name character varying(128) NOT NULL,
    author character varying(128),
    url character varying(128),
    state character varying(16),
    latest_version character varying(64),
    shortdesc character varying(256),
    category_id integer REFERENCES ir_module_category ON DELETE SET NULL,
    certificate character varying(64),
    description text,
    demo boolean DEFAULT False,
    web boolean DEFAULT FALSE,
    license VARCHAR(26),
    PRIMARY KEY(id)
) WITHOUT OIDS;
ALTER TABLE ir_module_module add constraint name_uniq unique (name);

CREATE TABLE ir_module_module_dependency (
    id serial NOT NULL,
    create_uid integer REFERENCES res_users ON DELETE SET NULL,
    create_date timestamp WITHOUT time zone,
    write_date timestamp WITHOUT time zone,
    write_uid integer REFERENCES res_users ON DELETE SET NULL,
    name character varying(128),
    version_pattern character varying(128) DEFAULT NULL,
    module_id integer REFERENCES ir_module_module ON DELETE cascade,
    PRIMARY KEY(id)
) WITHOUT OIDS;

CREATE TABLE res_company (
    id serial NOT NULL,
    name character varying(64) NOT NULL,
    parent_id integer REFERENCES res_company ON DELETE SET NULL,
    PRIMARY KEY(id)
) WITHOUT OIDS;

CREATE TABLE res_lang (
    id serial PRIMARY KEY,
    name VARCHAR(64) NOT NULL UNIQUE,
    code VARCHAR(16) NOT NULL UNIQUE
) WITHOUT OIDS;

CREATE TABLE ir_model_data (
    id serial NOT NULL PRIMARY KEY,
    create_uid integer,
    create_date timestamp WITHOUT time zone,
    write_date timestamp WITHOUT time zone,
    write_uid integer,
    noupdate boolean,
    name character varying(128) NOT NULL,
    date_init timestamp WITHOUT time zone,
    date_update timestamp WITHOUT time zone,
    module character varying(64) NOT NULL,
    model character varying(64) NOT NULL,
    res_id integer
) WITHOUT OIDS;

CREATE INDEX ir_model_data_name_index ON ir_model_data (name);
CREATE INDEX ir_model_data_model_index ON ir_model_data (model);

---------------------------------
-- Users
---------------------------------

INSERT INTO res_users (id,login,password,name,active,company_id,context_lang) VALUES (1,'admin','admin','Administrator',True,1,'en_US');
INSERT INTO ir_model_data (name,module,model,noupdate,res_id) VALUES ('user_root','base','res.users',True,1);

-- Compatibility purpose, to remove V6.0
INSERT INTO ir_model_data (name,module,model,noupdate,res_id) VALUES ('user_admin','base','res.users',True,1);

SELECT setval('res_users_id_seq', 2);
