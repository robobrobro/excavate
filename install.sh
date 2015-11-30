#!/bin/bash

NAME=excavate
SRC="./${NAME}.py"

# Find a directory where executables (namely, Python) are stored
# for the GitLab CI runner user, if one exists. Otherwise, install
# to /usr/local/bin.
GITLAB_USER=$(grep -i '^gitlab[^:]*runner:' /etc/passwd | cut -f1 -d:)
if [ "$GITLAB_USER" != "" ]; then
    DST_DIR=$(dirname $(su $GITLAB_USER -c "which python"))
else
    DST_DIR=/usr/local/bin
fi
DST="$DST_DIR/$NAME"

CP=/bin/cp
CHMOD=/bin/chmod
MD5SUM=md5sum

function exec_cmd() {
    local cmd="$@"
    $cmd

    local result=$?
    if [ $result -ne 0 ]; then
        exit $result
    fi
}

echo "Installing $SRC to $DST"
echo "------------------------------------------------------------------"
exec_cmd $MD5SUM $SRC
exec_cmd $CP $SRC $DST
exec_cmd $CHMOD +x $DST
exec_cmd $MD5SUM $DST
echo "------------------------------------------------------------------"
echo "Installation complete"
