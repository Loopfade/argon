export SCRIPT_DIR=$(realpath $(dirname $0))

replace() {
    export org=$2 new=$3
    find $1 -type f -exec sed -i 's@'$org'@'$new'@g' {} \;
}
