# Repair rules: teach the library a rewrite

When you fix a near-match by rewriting one C shape into another, and the same shape will turn up in other functions,
save the rewrite as a rule with `add_repair_rule(function, rule)`. The draft pass, the beam and the Deep Permuter then
try it on every function of the game, and it is judged like any other repair: a rule only proposes C, the judge decides.

A rule is a JSON object. This is the library's own `abs_idiom` rule, two sites and all (a hand-written absolute value becomes `abs()`):

```json
{
  "schema": 1,
  "id": "abs_idiom",
  "families": [
    "abs_idiom",
    "abs_idiom_one"
  ],
  "archs": [
    "mips"
  ],
  "order": 63,
  "gate": [
    {
      "asm_has": [
        "negu",
        "neg",
        {
          "op": [
            "subu",
            "sub"
          ],
          "argc": 3,
          "arg1": "$zero"
        }
      ]
    }
  ],
  "sites": [
    {
      "match": "^(?P<lead>[ \\t]*)(?P<var>@IDENT@)\\s*=\\s*(?P<expr>[^;{}]+?)\\s*;[ \\t]*\\n[ \\t]*if\\s*\\(\\s*(?P<test>[^;{}]+?)\\s*<\\s*0\\s*\\)\\s*\\{\\s*(?P=var)\\s*=\\s*-\\s*(?P=var)\\s*;\\s*\\}(?!\\s*else\\b)",
      "where": [
        {
          "group_equals_any": {
            "group": "test",
            "of": [
              "var",
              "expr"
            ]
          }
        },
        {
          "declared_type": {
            "group": "var",
            "kind": "integer"
          }
        }
      ],
      "replace": "{lead}{var} = abs({expr:strip});"
    },
    {
      "match": "^(?P<lead>[ \\t]*)if\\s*\\(\\s*(?P<var>@IDENT@)\\s*<\\s*0\\s*\\)\\s*\\{\\s*(?P=var)\\s*=\\s*-\\s*(?P=var)\\s*;\\s*\\}(?!\\s*else\\b)",
      "where": [
        {
          "declared_type": {
            "group": "var",
            "kind": "integer"
          }
        }
      ],
      "replace": "{lead}{var} = abs({var});"
    }
  ],
  "prepend_unless_declared": {
    "pattern": "^[ \\t]*(?:extern[ \\t]+)?(?:int|s32)[ \\t]+abs\\s*\\(",
    "text": "int abs(int);\n"
  },
  "children": {
    "all": "abs_idiom",
    "each": {
      "family": "abs_idiom_one",
      "when_sites_over": 1,
      "max": 7
    }
  }
}
```

- `id` and every family are lower-case names. A rule is saved as `<id>.json`; adding the same id again replaces it, which
  is how you correct one. An id of a rule the library ships is refused.
- `gate` (optional) makes the rule do nothing unless the target's assembly has one of the listed instructions.
- `sites`: `match` is a Python regular expression (multi-line) run on the function body with comments and strings
  blanked; `@IDENT@` is a C identifier, `@INTEGER@` an integer type. Each `{group}` in `replace` is that group's text
  (`{group:strip}` without surrounding blanks). A later site never takes text an earlier site took. `where` guards:
  `group_equals_any` and `declared_type` (the named group is declared an integer in the function).
- `children.all` is one child with every site rewritten; `children.each` adds one child per site.
- Escape every backslash in the JSON string (`\\s`, `\\t`, `\\(`). A pattern that repeats a repeated group, like `(a+)+`,
  is refused, and so is one over 2,000 characters.

The tool tries the rule on `function`'s current C and saves it only when it makes at least one child there; its answer
shows what each child changes. Then try a child with `compile_variants`. A rule that only proposes the right C on one
function is still worth saving if the shape is a compiler idiom; one that mentions a single variable name is not.
A rewrite that needs to walk blocks or track scopes can't be written as a rule: report it with `report_repair`.
