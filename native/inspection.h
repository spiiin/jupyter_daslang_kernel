#include "daScript/ast/ast_visitor.h"
#include <set>
#include <sstream>
#include <algorithm>

// Compile-time inspection uses a temporary Program; never simulate its user code.
extern "C" __declspec(dllexport) void nb_inspection_policy(void *policy) {
    auto &p = *static_cast<das::CodeOfPolicies *>(policy);
    p.no_optimizations = true;
    p.ignore_shared_modules = true;
    p.export_all = true; // Preserve unused user and macro-generated functions for inspection.
}

struct NotebookTreePrinter : das::Visitor {
    std::string nodes;
    int depth = 0;
    unsigned count = 0;
    // Constants dispatch both a base and a concrete callback; record the latter once.
    void preVisit(das::ExprConst *) override {}
    das::ExpressionPtr visit(das::ExprConst *expr) override { return expr; }
    bool canVisitQuoteSubexpression(das::ExprQuote *) override { return true; }
    void preVisitExpression(das::Expression *expr) override {
        if (count++ < 10000) {
            if (!nodes.empty()) nodes += ',';
            nodes += "{\"depth\":" + std::to_string(depth) + ",\"kind\":";
            nodes += notebookJson(expr->__rtti ? expr->__rtti : "Expression");
            nodes += ",\"type\":" + notebookJson(expr->type ? expr->type->describe().c_str() : "");
            if (expr->rtti_isConstant() || expr->rtti_isVar()) {
                nodes += ",\"value\":" + notebookJson(expr->describe().c_str());
            }
            nodes += '}';
        }
        ++depth;
    }
    das::ExpressionPtr visitExpression(das::Expression *expr) override {
        --depth;
        return expr;
    }
};

extern "C" __declspec(dllexport) const char *nb_inspect_program(void *program, const char *selection, int tree) {
    static thread_local std::string output;
    auto &prog = *static_cast<das::Program *>(program);
    std::set<std::string> names;
    std::istringstream input(selection ? selection : "");
    for (std::string name; std::getline(input, name);) names.insert(name);
    auto selected = [&](const auto &name) {
        return names.count(name.c_str()) || (names.count("*") && !names.count("-" + std::string(name.c_str())));
    };
    std::vector<std::pair<std::string, std::string>> functions;
    prog.getThisModule()->functions.foreach([&](auto fn) {
        if (!selected(fn->name)) return;
        das::TextWriter source;
        source << *fn;
        std::string record = "{\"name\":" + notebookJson(fn->name.c_str());
        record += ",\"source\":" + notebookJson(source.str().c_str());
        if (tree && fn->body) {
            NotebookTreePrinter visitor;
            fn->body->visit(visitor);
            record += ",\"nodes\":[" + visitor.nodes + "],\"truncated\":";
            record += visitor.count > 10000 ? "true" : "false";
        }
        record += '}';
        functions.emplace_back(fn->name.c_str(), std::move(record));
    });
    std::sort(functions.begin(), functions.end());
    output = "{\"functions\":[";
    for (size_t i = 0; i < functions.size(); ++i) {
        if (i) output += ',';
        output += functions[i].second;
    }
    output += "],\"structures\":[";
    bool firstStructure = true;
    prog.getThisModule()->structures.foreach([&](auto st) {
        if (!selected(st->name)) return;
        if (!firstStructure) output += ',';
        firstStructure = false;
        output += "{\"name\":" + notebookJson(st->name.c_str()) + ",\"fields\":[";
        bool firstField = true;
        for (auto &field : st->fields) {
            if (!firstField) output += ',';
            firstField = false;
            output += "{\"name\":" + notebookJson(field.name.c_str());
            output += ",\"type\":" + notebookJson(field.type ? field.type->describe().c_str() : "");
            if (field.init) {
                das::TextWriter initializer;
                initializer << *field.init;
                output += ",\"initializer\":" + notebookJson(initializer.str().c_str());
            }
            output += '}';
        }
        output += "]}";
    });
    output += "]}";
    return output.c_str();
}
