#pragma once
#include "module_state.h"
#include "daScript/simulate/runtime_array.h"
#include "daScript/simulate/runtime_table.h"
#include <stdexcept>
#include <cstring>

// A value codec for module-private globals, whose names/types need not be
// accessible from the generated notebook's Archive helper functions.
struct NotebookCodec {
    NotebookContext &ctx;
    std::vector<uint8_t> output;
    const uint8_t *input = nullptr;
    size_t length = 0;
    size_t position = 0;
    bool reading = false;

    static bool scalar(das::Type type) {
        using das::Type;
        switch (type) {
        case Type::tBool: case Type::tInt: case Type::tInt8: case Type::tInt16: case Type::tInt64:
        case Type::tUInt: case Type::tUInt8: case Type::tUInt16: case Type::tUInt64:
        case Type::tFloat: case Type::tDouble:
        case Type::tEnumeration: case Type::tEnumeration8: case Type::tEnumeration16: case Type::tEnumeration64:
            return true;
        default: return false;
        }
    }
    static void check(das::TypeInfo *info, int depth = 0) {
        if (!info || depth > 64 || info->isRef())
            throw std::runtime_error("unsupported module global reference or recursive type");
        if (info->dimSize) {
            auto element = *info;
            element.dimSize = 0; element.dim = nullptr;
            check(&element, depth + 1); return;
        }
        if (scalar(info->type) || info->type == das::Type::tString) return;
        if (info->type == das::Type::tArray) { check(info->firstType, depth + 1); return; }
        if (info->type == das::Type::tTable) {
            if (!info->firstType || info->firstType->dimSize ||
                (!scalar(info->firstType->type) && info->firstType->type != das::Type::tString))
                throw std::runtime_error("module table keys must be scalar or string");
            check(info->secondType, depth + 1); return;
        }
        if (info->type == das::Type::tStructure) {
            auto *st = info->getStructType();
            if (!st || (st->flags & das::StructInfo::flag_class))
                throw std::runtime_error("module classes and resource handles are not checkpointable yet");
            for (uint32_t i = 0; i < st->count; ++i) check(st->fields[i], depth + 1);
            return;
        }
        throw std::runtime_error("unsupported module global type (pointers, handles, closures and iterators are not checkpointable)");
    }
    void transfer(void *value, size_t size) {
        if (reading) {
            if (size > length - position) throw std::runtime_error("truncated module checkpoint");
            if (size) std::memcpy(value, input + position, size);
            position += size;
        } else {
            auto *bytes = static_cast<uint8_t *>(value);
            output.insert(output.end(), bytes, bytes + size);
        }
    }
    uint64_t count(uint64_t value = 0) {
        transfer(&value, sizeof(value));
        if (reading && value > length) throw std::runtime_error("invalid module checkpoint length");
        return value;
    }
    template <typename Key> int64_t insert(das::Table &table, char *key, uint32_t size) {
        Key value;
        std::memcpy(&value, key, sizeof(value));
        das::TableHash<Key> hash(&ctx, size);
        return hash.reserve(table, value, das::KeyHash<Key>()(ctx, value), nullptr);
    }
    int64_t insert(das::Table &table, char *key, das::TypeInfo *type, uint32_t size) {
        using das::Type;
        switch (type->type) {
        case Type::tString: return insert<char *>(table, key, size);
        case Type::tBool: return insert<bool>(table, key, size);
        case Type::tInt8: return insert<int8_t>(table, key, size);
        case Type::tEnumeration8: return (type->getEnumType()->flags & das::EnumInfo::flag_unsigned) ? insert<uint8_t>(table, key, size) : insert<int8_t>(table, key, size);
        case Type::tUInt8: return insert<uint8_t>(table, key, size);
        case Type::tInt16: return insert<int16_t>(table, key, size);
        case Type::tEnumeration16: return (type->getEnumType()->flags & das::EnumInfo::flag_unsigned) ? insert<uint16_t>(table, key, size) : insert<int16_t>(table, key, size);
        case Type::tUInt16: return insert<uint16_t>(table, key, size);
        case Type::tInt: return insert<int32_t>(table, key, size);
        case Type::tEnumeration: return (type->getEnumType()->flags & das::EnumInfo::flag_unsigned) ? insert<uint32_t>(table, key, size) : insert<int32_t>(table, key, size);
        case Type::tUInt: return insert<uint32_t>(table, key, size);
        case Type::tInt64: return insert<int64_t>(table, key, size);
        case Type::tEnumeration64: return (type->getEnumType()->flags & das::EnumInfo::flag_unsigned) ? insert<uint64_t>(table, key, size) : insert<int64_t>(table, key, size);
        case Type::tUInt64: return insert<uint64_t>(table, key, size);
        case Type::tFloat: return insert<float>(table, key, size);
        case Type::tDouble: return insert<double>(table, key, size);
        default: throw std::runtime_error("unsupported table key");
        }
    }
    void walk(char *data, das::TypeInfo *info) {
        using das::Type;
        if (info->dimSize) {
            uint64_t count = 1;
            for (uint32_t i = 0; i < info->dimSize; ++i) {
                if (info->dim[i] && count > UINT64_MAX / info->dim[i]) throw std::runtime_error("invalid fixed array size");
                count *= info->dim[i];
            }
            auto element = *info;
            element.dimSize = 0; element.dim = nullptr;
            element.size = count ? uint32_t(info->size / count) : 0;
            for (uint64_t i = 0; i < count; ++i) walk(data + i * element.size, &element);
            return;
        }
        if (scalar(info->type)) { transfer(data, info->size); return; }
        if (info->type == Type::tString) {
            auto &value = *reinterpret_cast<char **>(data);
            uint64_t size = count(value ? uint64_t(das::stringLengthSafe(ctx, value)) + 1 : 0);
            if (reading) {
                if (!size) value = nullptr;
                else {
                    if (size - 1 > length - position) throw std::runtime_error("truncated string");
                    value = ctx.allocateString(reinterpret_cast<const char *>(input + position), uint32_t(size - 1), nullptr);
                    position += size - 1;
                }
            } else if (size) transfer(value, size - 1);
            return;
        }
        if (info->type == Type::tStructure) {
            auto *st = info->getStructType();
            for (uint32_t i = 0; i < st->count; ++i) walk(data + st->fields[i]->offset, st->fields[i]);
            return;
        }
        if (info->type == Type::tArray) {
            auto &array = *reinterpret_cast<das::Array *>(data);
            const uint64_t size = count(array.size);
            const uint32_t stride = info->firstType->size;
            if (reading) das::array_resize(ctx, array, size, stride, true, nullptr);
            for (uint64_t i = 0; i < size; ++i) walk(array.data + i * stride, info->firstType);
            return;
        }
        if (info->type == Type::tTable) {
            auto &table = *reinterpret_cast<das::Table *>(data);
            const uint64_t size = count(table.size);
            const uint32_t keySize = info->firstType->size, valueSize = info->secondType->size;
            if (reading) {
                if (table.size || table.capacity) throw std::runtime_error("module table restore requires an empty destination");
                for (uint64_t i = 0; i < size; ++i) {
                    alignas(16) char key[16]{};
                    if (keySize > sizeof(key)) throw std::runtime_error("unsupported table key size");
                    walk(key, info->firstType);
                    const int64_t slot = insert(table, key, info->firstType, valueSize);
                    walk(table.data + slot * valueSize, info->secondType);
                }
            } else for (uint64_t i = 0; i < table.capacity; ++i) {
                if (das::tableLiveSlot(table, i)) {
                    walk(table.keys + i * keySize, info->firstType);
                    walk(table.data + i * valueSize, info->secondType);
                }
            }
        }
    }
};

extern "C" __declspec(dllexport) const char *nb_module_value_error(void *context) {
    auto &ctx = *static_cast<NotebookContext *>(context);
    ctx.error.clear();
    try {
        for (const auto &var : ctx.imported) {
            try {
                if (ctx.globalVariables[var.index].shared) throw std::runtime_error("shared-memory module globals are not checkpointable yet");
                NotebookCodec::check(ctx.globalVariables[var.index].debugInfo);
            }
            catch (const std::exception &error) {
                throw std::runtime_error(var.module + "::" + var.name + ": " + error.what());
            }
        }
    } catch (const std::exception &error) { ctx.error = error.what(); }
    return ctx.error.c_str();
}

extern "C" __declspec(dllexport) const char *nb_save_module_value(void *context, int index) {
    auto &ctx = *static_cast<NotebookContext *>(context);
    ctx.error.clear();
    try {
        NotebookCodec codec{ctx};
        const bool ok = ctx.runWithCatch([&]() { codec.walk(static_cast<char *>(ctx.getVariable(index)), ctx.globalVariables[index].debugInfo); });
        if (!ok) throw std::runtime_error(ctx.getException());
        ctx.bytes = std::move(codec.output);
    } catch (const std::exception &error) { ctx.error = error.what(); }
    return ctx.error.c_str();
}

extern "C" __declspec(dllexport) const void *nb_module_bytes(void *context) {
    return static_cast<NotebookContext *>(context)->bytes.data();
}
extern "C" __declspec(dllexport) uint64_t nb_module_bytes_size(void *context) {
    return static_cast<NotebookContext *>(context)->bytes.size();
}
extern "C" __declspec(dllexport) const char *nb_load_module_value(void *context, int index, const void *bytes, uint64_t size) {
    auto &ctx = *static_cast<NotebookContext *>(context);
    ctx.error.clear();
    try {
        NotebookCodec codec{ctx};
        codec.input = static_cast<const uint8_t *>(bytes);
        codec.length = size;
        codec.reading = true;
        const bool ok = ctx.runWithCatch([&]() {codec.walk(static_cast<char *>(ctx.getVariable(index)), ctx.globalVariables[index].debugInfo);});
        if (!ok) throw std::runtime_error(ctx.getException());
        if (codec.position != size) throw std::runtime_error("module checkpoint has trailing data");
        nb_skip_global_init(&ctx, index);
    } catch (const std::exception &error) { ctx.error = error.what(); }
    return ctx.error.c_str();
}
