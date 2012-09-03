DATABASE=trunk
dropdb ${DATABASE}
REPOSITORIES=../../addons/trunk
MODULES=`python -c "import os; print ','.join(os.listdir('${REPOSITORIES}'))"`
createdb ${DATABASE}
rm openerp-server.log
./openerp-server \
    --log-level=debug \
    --addons=${REPOSITORIES},../../web/trunk/addons \
    -d ${DATABASE} \
    -i ${MODULES} \
    --stop-after-init \
    --no-xmlrpc \
    --no-xmlrpcs \
    --no-netrpc \
    --test-enable \
    --logfile=openerp-server.log 
