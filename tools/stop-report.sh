#!/bin/bash

DB="openerp"
PASSWD=
HOSTNAME=localhost
PORT=8069
USERID=1
REPORT=1

method_1() {
	cat '-' << EOF
<xml>
<methodCall>
	<methodName>report_stop</methodName>
	<params>
		<param><value><string>$DB</string></value></param>
		<param><value><int>$USERID</int></value></param>
		<param><value><string>$PASSWD</string></value></param>
		<param><value><int>$REPORT</int></value></param>
	</params>
</methodCall>
EOF
}

while [ -n "$1" ] ; do
    case "$1" in
	-d)
	    DB="$2"
	    shift 2
	    ;;
	-H)
	    HOSTNAME="$2"
	    shift 2
	    ;;
	-p)
	    PORT="$2"
	    shift 2
	    ;;
	-U)
	    USERID="$2"
	    shift 2
	    ;;
	*)
	    break
	    ;;
    esac
done

if [ -n "$1" ] ; then
	REPORT="$1"
fi

if [ -z "$PASSWD" ] ; then
    read -s -p "Enter the password for $DB:" PASSWD
    echo
fi

method_1 | POST -c 'text/xml' http://$HOSTNAME:$PORT/xmlrpc/report

#eof
