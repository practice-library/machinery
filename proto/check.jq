# Checks a buf image (buf build --exclude-imports -o image.json) of machinery.plugin.v1alpha1:
# - the service has exactly the calls Describe, Plan, RunUnit, Merge and Run, each with <Call>Request and
#   <Call>Response;
# - every response, and the Failure detail, is exactly the shared reply format (envelope.schema.json): the
#   same numbers, names, labels, types and presence;
# - every request is exactly its fields: caps = 1, then network (RunUnit and Run only) and input (all but
#   Describe), each singular, of their types.
# Prints each finding and exits 1 when there is one, or when the package or its calls are not found at all.
def sig: [.field[] | "\(.number) \(.name) \(.label) \(.type) \(.typeName // "") \(.proto3Optional // false)"];
"machinery.plugin.v1alpha1" as $pkg
| [.file[] | select(.package == $pkg)] as $files
| [
    "1 ok LABEL_OPTIONAL TYPE_BOOL  false",
    "2 data LABEL_OPTIONAL TYPE_MESSAGE .google.protobuf.Value false",
    "3 not_read LABEL_REPEATED TYPE_MESSAGE .\($pkg).NotRead false",
    "4 warnings LABEL_REPEATED TYPE_STRING  false"
  ] as $envelope
| "1 caps LABEL_OPTIONAL TYPE_MESSAGE .\($pkg).Caps false" as $caps
| {
    Describe: [$caps],
    Plan: [$caps, "2 input LABEL_OPTIONAL TYPE_MESSAGE .google.protobuf.Struct false"],
    RunUnit: [$caps, "2 network LABEL_OPTIONAL TYPE_MESSAGE .\($pkg).Network false", "3 input LABEL_OPTIONAL TYPE_MESSAGE .google.protobuf.Struct false"],
    Merge: [$caps, "2 input LABEL_OPTIONAL TYPE_MESSAGE .google.protobuf.Struct false"],
    Run: [$caps, "2 network LABEL_OPTIONAL TYPE_MESSAGE .\($pkg).Network false", "3 input LABEL_OPTIONAL TYPE_MESSAGE .google.protobuf.Struct false"]
  } as $requests
| ([$files[].messageType[] | {key: ".\($pkg).\(.name)", value: .}] | from_entries) as $m
| [$files[].service[]?.method[]?] as $calls
| [
    (if ($files | length) == 0 then "no file of package \($pkg)" else empty end),
    (if ([$calls[].name] | sort) != ($requests | keys | sort)
     then "the calls are \([$calls[].name] | sort), not \($requests | keys | sort)" else empty end),
    (if $m[".\($pkg).Failure"] == null then "no Failure message"
     elif ($m[".\($pkg).Failure"] | sig) != $envelope then "Failure is not exactly the envelope fields" else empty end),
    ($calls[]
     | .name as $n
     | (if .inputType != ".\($pkg).\($n)Request" or .outputType != ".\($pkg).\($n)Response"
        then "\($n): takes \(.inputType) and returns \(.outputType)" else empty end),
       (if $m[.outputType] == null or ($m[.outputType] | sig) != $envelope
        then "\($n): \(.outputType) is not exactly the envelope fields ok, data, not_read, warnings" else empty end),
       (if $m[.inputType] == null or ($m[.inputType] | sig) != ($requests[$n] // [])
        then "\($n): \(.inputType) is not exactly its fields \($requests[$n] // [] | map(split(" ")[1]))" else empty end))
  ] as $findings
| if ($findings | length) > 0
  then ($findings | map("::error file=proto/machinery/plugin/v1alpha1/plugin.proto::\(.)") | join("\n")) + "\n" | halt_error(1)
  else "the five calls; each request exactly its fields; every reply and Failure exactly the envelope"
  end
