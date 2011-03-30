#!/bin/bash

ADMIN_PASSWD='admin'
method_1() {
	cat '-' << EOF
<xml>
<methodCall>
	<methodName>set_pool_debug</methodName>
	<params>
		<param><value><string>$ADMIN_PASSWD</string></value></param>
		<param><value><string>$DBNAME</string></value></param>
		<param><value>$DO_DEBUG</value></param>
	</params>
</methodCall>
</xml>
EOF
}

if [ -z "$1" ] ; then
	echo "Must supply dbname "
	exit 2
fi
DBNAME="$1"
DO_DEBUG='<boolean>1</boolean>'
shift 1

if [ -n "$1" ] ; then 
	if [ "$1" == '1' ] || [ "$1" == 'true' ] ;then
		DO_DEBUG='<boolean>1</boolean>'
	else
		DO_DEBUG='<boolean>0</boolean>'
	fi
fi

method_1 | POST -c 'text/xml' http://localhost:8069/xmlrpc/common

#eof
