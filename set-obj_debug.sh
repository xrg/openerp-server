#!/bin/bash

ADMIN_PASSWD='admin'
method_1() {
	cat '-' << EOF
<xml>
<methodCall>
	<methodName>set_obj_debug</methodName>
	<params>
		<param><value><string>$ADMIN_PASSWD</string></value></param>
		<param><value><string>$DBNAME</string></value></param>
		<param><value><string>$OBJNAME</string></value></param>
		<param><value>$DO_DEBUG</value></param>
	</params>
</methodCall>
EOF
}

if [ -z "$2" ] ; then
	echo "Must supply dbname and object name at least"
	exit 2
fi
DBNAME="$1"
OBJNAME="$2"
DO_DEBUG='<boolean>1</boolean>'
shift 2

if [ -n "$1" ] ; then 
	if [ "$1" == '1' ] || [ "$1" == 'true' ] ;then
		DO_DEBUG='<boolean>1</boolean>'
	else
		DO_DEBUG='<boolean>0</boolean>'
	fi
fi

method_1 | POST -c 'text/xml' http://localhost:8069/xmlrpc/common

#eof
