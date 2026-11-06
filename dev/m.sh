#!/bin/sh
# m.sh eval 'js' | m.sh click X Y | m.sh type 'text' | m.sh shot | m.sh key Enter
N="timeout 15 $HOME/.local/bin/node"; T=${TAB:-tagmanager}; D=$(dirname "$0")
case "$1" in
  eval) $N $D/cdp.mjs $T Runtime.evaluate "$(python3 -c 'import json,sys; print(json.dumps({"expression": sys.argv[1], "returnByValue": True, "awaitPromise": True}))' "$2")" | python3 -c 'import json,sys; r=json.load(sys.stdin); print(r.get("result",{}).get("value", r))' ;;
  click) for t in mousePressed mouseReleased; do $N $D/cdp.mjs $T Input.dispatchMouseEvent "{\"type\":\"$t\",\"x\":$2,\"y\":$3,\"button\":\"left\",\"clickCount\":1}" >/dev/null; done ;;
  type) $N $D/cdp.mjs $T Input.insertText "$(python3 -c 'import json,sys; print(json.dumps({"text": sys.argv[1]}))' "$2")" >/dev/null ;;
  shot) $N $D/cdp.mjs $T Page.captureScreenshot '{}' ; sleep 1 ;;
esac
