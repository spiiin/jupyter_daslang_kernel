options gen2
module notebook_macros public
require daslib/ast
require daslib/ast_boost
require daslib/templates_boost
require daslib/macro_boost
require daslib/ast_verify
[call_macro(name="plus_one")]
class PlusOneMacro : AstCallMacro {
    def override visit(prog:ProgramPtr; mod:Module?; var expr:ExprCallMacro?):ExpressionPtr {
        macro_verify(length(expr.arguments)==1, prog, expr.at, "plus_one expects one argument")
        var result <- qmacro($e(clone_expression(expr.arguments[0])) + 1)
        verify_expression(result)
        return <- result
    }
}
