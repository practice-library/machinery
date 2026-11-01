# Checks a buf image (buf build --exclude-imports -o image.json) of machinery.plugin.v1alpha1. Every call's
# response, and the Failure detail, is exactly the shared reply format (envelope.schema.json): the same
# numbers, names, labels, types and presence. Every request starts with caps = 1; every request but
# Describe's carries input; RunUnit's and Run's carry network. Prints each finding and exits 1 when there is
# one, or when the package or its calls are not found at all.
def sig: [.field[] | "\(.number) \(.name) \(.label) \(.type) \(.typeName // "") \(.proto3Optional // false)"];
def has($m; $line): ($m | sig | index([$line])) != null;
"machinery.plugin.v1alpha1" as $pkg
| [.file[] | select(.package == $pkg)] as $files
| [
    "1 ok LABEL_OPTIONAL TYPE_BOOL  false",
    "2 data LABEL_OPTIONAL TYPE_MESSAGE .google.protobuf.Value false",
    "3 not_read LABEL_REPEATED TYPE_MESSAGE .\($pkg).NotRead false",
    "4 warnings LABEL_REPEATED TYPE_STRING  false"
  ] as $envelope
| ([$files[].messageType[] | {key: ".\($pkg).\(.name)", value: .}] | from_entries) as $m
| [$files[].service[]?.method[]?] as $calls
| [
    (if ($files | length) == 0 then "no file of package \($pkg)" else empty end),
    (if ($calls | length) == 0 then "no calls in package \($pkg)" else empty end),
    (if $m[".\($pkg).Failure"] == null then "no Failure message"
     elif ($m[".\($pkg).Failure"] | sig) != $envelope then "Failure is not exactly the envelope fields" else empty end),
    ($calls[]
     | .name as $n
     | (if $m[.outputType] == null or ($m[.outputType] | sig) != $envelope
        then "\($n): \(.outputType) is not exactly the envelope fields ok, data, not_read, warnings" else empty end),
       (if $m[.inputType] == null then "\($n): \(.inputType) is not in the package"
        else $m[.inputType] as $req
        | (if ($req | sig | first) != "1 caps LABEL_OPTIONAL TYPE_MESSAGE .\($pkg).Caps false"
           then "\($n): \(.inputType) does not start with caps = 1" else empty end),
          (if $n != "Describe" and ([$req.field[] | select(.name == "input" and .typeName == ".google.protobuf.Struct")] | length) != 1
           then "\($n): \(.inputType) has no input Struct" else empty end),
          (if ($n == "RunUnit" or $n == "Run") and ([$req.field[] | select(.name == "network" and .typeName == ".\($pkg).Network")] | length) != 1
           then "\($n): \(.inputType) has no network" else empty end)
        end))
  ] as $findings
| if ($findings | length) > 0
  then ($findings | map("::error file=proto/machinery/plugin/v1alpha1/plugin.proto::\(.)") | join("\n")) + "\n" | halt_error(1)
  else "every call: caps first, input where needed, network for RunUnit and Run, envelope reply; Failure is the envelope"
  end
