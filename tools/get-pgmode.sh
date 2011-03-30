#!/bin/bash

ADMIN_PASSWD='admin'
method_1() {
	cat '-' << EOF
<xml>
<methodCall>
	<methodName>get_pgmode</methodName>
	<params>
		<param><value><string>$ADMIN_PASSWD</string></value></param>
	</params>
</methodCall>
</xml>
EOF
}

method_1 | POST -c 'text/xml' http://localhost:8069/xmlrpc/common

#eof
