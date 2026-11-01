# Checks a buf image (buf build --exclude-imports -o -#format=json) of machinery.plugin.v1alpha1:
# every call's response carries exactly the fields of the shared reply format (envelope.schema.json),
# and every call's request carries the caps first. Prints each finding; exits 1 when there is one.
def sig: [.field[] | "\(.number) \(.name) \(.label) \(.type) \(.typeName // "")"];
.file[] | select(.package == "machinery.plugin.v1alpha1") as $f
| ($f.messageType | map({key: ".\($f.package).\(.name)", value: .}) | from_entries) as $m
| [
    "1 ok LABEL_OPTIONAL TYPE_BOOL ",
    "2 data LABEL_OPTIONAL TYPE_MESSAGE .google.protobuf.Value",
    "3 not_read LABEL_REPEATED TYPE_MESSAGE .machinery.plugin.v1alpha1.NotRead",
    "4 warnings LABEL_REPEATED TYPE_STRING "
  ] as $envelope
| [ $f.service[].method[]
    | (if ($m[.outputType] | sig) != $envelope
       then "\(.name): \(.outputType) is not exactly the envelope fields ok, data, not_read, warnings" else empty end),
      (if ($m[.inputType] | sig | first) != "1 caps LABEL_OPTIONAL TYPE_MESSAGE .machinery.plugin.v1alpha1.Caps"
       then "\(.name): \(.inputType) does not start with caps = 1" else empty end)
  ] as $findings
| ($findings[] | "::error file=proto/machinery/plugin/v1alpha1/plugin.proto::\(.)"),
  (if ($findings | length) > 0 then halt_error(1) else "every call: caps first, envelope reply" end)
