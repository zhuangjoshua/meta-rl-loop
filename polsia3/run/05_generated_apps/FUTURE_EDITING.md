# Future User Follow-Up Editing

The template system must be easy to edit later.

Future editing should support:
- "make the homepage more enterprise"
- "change pricing"
- "add a workflow step"
- "make the app less purple"
- "add a dashboard tab"
- "change the prompt used by this AI action"

Preferred edit order:
1. update typed config
2. swap block/module variant
3. edit bounded module
4. only then edit broad app code

Every edit should run typecheck/build/smoke tests before deploy.

