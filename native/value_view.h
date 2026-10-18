#pragma once
#include "daScript/simulate/debug_print.h"
#include <cmath>

// Reflect live checkpoint-compatible data, never invoke user functions/getters.
struct NotebookValueView {
    NotebookContext &ctx;
    uint32_t nodes=0, count, depthLimit;
    std::string walk(char *data,das::TypeInfo *info,uint32_t depth,uint64_t start=0) {
        auto output=value(data,info,depth,start);
        output.insert(1,"\"type\":"+notebookJson(das::debug_type(info).c_str())+",");
        return output;
    }
    std::string value(char *data,das::TypeInfo *info,uint32_t depth,uint64_t start) {
        using das::Type;
        if (++nodes>500 || depth>depthLimit) return "{\"kind\":\"truncated\"}";
        if (info->type==Type::tArray) {
            auto &a=*reinterpret_cast<das::Array *>(data);
            std::string out="{\"kind\":\"array\",\"size\":"+std::to_string(a.size)+",\"start\":"+std::to_string(start)+",\"items\":[";
            uint64_t end=std::min<uint64_t>(a.size,start+count);
            for (uint64_t i=start;i<end && nodes<500;++i) {
                if (i!=start) out+=',';
                out+="{\"index\":"+std::to_string(i)+",\"value\":"+walk(a.data+i*info->firstType->size,info->firstType,depth+1)+"}";
            }
            return out+"]}";
        }
        if (info->type==Type::tTable) {
            auto &t=*reinterpret_cast<das::Table *>(data);
            std::string out="{\"kind\":\"table\",\"size\":"+std::to_string(t.size)+",\"start\":"+std::to_string(start)+",\"items\":[";
            uint64_t ordinal=0,shown=0;
            // Bound work even for a very sparse, previously huge hash table.
            uint64_t scan=std::min<uint64_t>(t.capacity,1000000);
            for (uint64_t i=0;i<scan && shown<count && nodes<498;++i) if (das::tableLiveSlot(t,i)) {
                if (ordinal++<start) continue;
                if (shown++) out+=',';
                out+="{\"key\":"+walk(t.keys+i*info->firstType->size,info->firstType,depth+1)
                    +",\"value\":"+walk(t.data+i*info->secondType->size,info->secondType,depth+1)+"}";
            }
            return out+"],\"scanLimited\":"+(t.capacity>scan?"true":"false")+"}";
        }
        if (info->type==Type::tStructure) {
            std::string out="{\"kind\":\"struct\",\"fields\":[";
            auto st=info->structType;
            uint32_t limit=std::min<uint32_t>(st->count,50);
            uint32_t shown=0;
            for (uint32_t i=0;i<limit && nodes<500;++i) {
                if (shown++) out+=',';
                auto field=st->fields[i];
                out+="{\"name\":"+notebookJson(field->name)+",\"value\":"+walk(data+field->offset,field,depth+1)+"}";
            }
            return out+"],\"size\":"+std::to_string(st->count)+"}";
        }
        if (info->type==Type::tString) {
            auto value=*reinterpret_cast<char **>(data);
            auto size=value?das::stringLengthSafe(ctx,value):0;
            std::string text=value?std::string(value,std::min<uint32_t>(size,200)):"";
            if (size>200) text+="...";
            return "{\"kind\":\"scalar\",\"size\":"+std::to_string(size)+",\"value\":"+notebookJson(text)+"}";
        }
        auto text=das::debug_json_value(data,info,false);
        if ((info->type==Type::tFloat && !std::isfinite(*reinterpret_cast<float *>(data))) ||
            (info->type==Type::tDouble && !std::isfinite(*reinterpret_cast<double *>(data)))) text=notebookJson(text.c_str());
        return "{\"kind\":\"scalar\",\"value\":"+std::string(text.c_str())+"}";
    }
};

extern "C" __declspec(dllexport) const char *nb_view_value(void *context,const char *name,uint32_t start,uint32_t count,uint32_t depth) {
    static thread_local std::string output;
    auto &ctx=*static_cast<NotebookContext *>(context);
    auto found=ctx.rootVariables.find(name?name:"");
    if (found==ctx.rootVariables.end()) {output="{\"error\":\"Unknown notebook variable\"}";return output.c_str();}
    NotebookValueView view{ctx,0,std::min<uint32_t>(count,100),std::min<uint32_t>(depth,8)};
    output=view.walk(static_cast<char *>(ctx.getVariable(found->second)),ctx.globalVariables[found->second].debugInfo,0,start);
    return output.c_str();
}
