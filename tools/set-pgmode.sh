#!/bin/bash

ADMIN_PASSWD='admin'
method_1() {
	cat '-' << EOF
<xml>
<methodCall>
	<methodName>set_pgmode</methodName>
	<params>
		<param><value><string>$ADMIN_PASSWD</string></value></param>
		<param><value><string>$1</string></value></param>
	</params>
</methodCall>
</xml>
EOF
}

if [ -z "$1" ] ; then
	echo "Usage: $0 {old|sql|pgsql|pg84} "
	exit 2
fi

method_1 $1 | POST -c 'text/xml' http://localhost:8069/xmlrpc/common

#eof
