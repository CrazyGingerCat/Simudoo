#!/bin/bash
set -e

#rm -r doc/source/apidoc/
pushd simudo
python3 -m sphinx.ext.apidoc -o ../doc/source/apidoc/ . memoize misc problemdata solution test trash
popd

make -C doc html

# webfsd -p 8777 -r doc/build/html/
