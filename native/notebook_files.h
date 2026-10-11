#pragma once
#include "daScript/simulate/fs_file_info.h"

using NotebookSourceCallback = const char *(*)(const char *, const char *, uint32_t, uint32_t *);

struct NotebookFileAccess : das::FsFileAccess {
    explicit NotebookFileAccess(NotebookSourceCallback transform) : callback(transform) {}
    NotebookSourceCallback callback;
    das::FileInfo *getNewFileInfo(const das::string &filename) override {
        auto *original = FsFileAccess::getNewFileInfo(filename);
        if (!original) return nullptr;
        const char *text = nullptr;
        uint32_t size = 0, transformedSize = 0;
        original->getSourceAndLength(text, size);
        const char *transformed = text ? callback(filename.c_str(), text, size, &transformedSize) : nullptr;
        if (!transformed) return original;
        char *owned = static_cast<char *>(das_aligned_alloc16(transformedSize + 1));
        std::memcpy(owned, transformed, transformedSize);
        owned[transformedSize] = 0;
        return setFileInfo(filename, das::FileInfoPtr(new das::TextFileInfo(owned, transformedSize, true)));
    }
};

extern "C" __declspec(dllexport) void *nb_fileaccess_make(NotebookSourceCallback callback) {
    auto *access = new NotebookFileAccess(callback);
    access->addRef();
    return access;
}
