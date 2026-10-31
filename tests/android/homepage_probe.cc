#include <jni.h>
#include <jvmti.h>
#include <math.h>
#include <stddef.h>
#include <pthread.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

static jvmtiEnv* ti;
static jniNativeInterface* original_jni;
static const jniNativeInterface* previous_jni;
static FILE* output;
static char patch_path[1024];
static char sync_method_name[128];
static bool syncing;
static pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;
static jmethodID readiness, library_loaded;
static jobject current_toolbar;
static jvmtiError toolbar_error = JVMTI_ERROR_ABSENT_INFORMATION;
static jclass view_group;
static jmethodID alpha_method, visibility_method, child_count, child_at;

static void release(void* value) {
  if (value) ti->Deallocate(static_cast<unsigned char*>(value));
}
static void remember(jclass cls) {
  char* signature = nullptr;
  if (ti->GetClassSignature(cls, &signature, nullptr) != JVMTI_ERROR_NONE) return;
  bool phone = !strcmp(signature, "Lorg/chromium/chrome/browser/toolbar/top/ToolbarPhone;");
  bool natives = !strcmp(signature, "LJ/N;");
  if (phone || natives) {
    jint count = 0;
    jmethodID* methods = nullptr;
    if (ti->GetClassMethods(cls, &count, &methods) == JVMTI_ERROR_NONE) {
      for (int i = 0; i < count; ++i) {
        char *name = nullptr, *desc = nullptr;
        ti->GetMethodName(methods[i], &name, &desc, nullptr);
        if (name && desc) {
          if (phone && !strcmp(name, "z") && !strncmp(desc, "()L", 3)) readiness = methods[i];
          if (natives && !strcmp(name, "ZI") && !strcmp(desc, "(II)Z")) library_loaded = methods[i];
        }
        release(name); release(desc);
      }
      release(methods);
    }
  }
  release(signature);
}
static void JNICALL prepared(jvmtiEnv*, JNIEnv*, jthread, jclass cls) { remember(cls); }
static void views(JNIEnv* env, jobject view, int* count, bool* hidden) {
  jclass cls = env->GetObjectClass(view);
  char* signature = nullptr;
  ti->GetClassSignature(cls, &signature, nullptr);
  float alpha = env->CallFloatMethod(view, alpha_method);
  int visibility = env->CallIntMethod(view, visibility_method);
  if ((*count)++) fputc(',', output);
  fprintf(output, "{\"class\":\"%s\",\"alpha\":%.6g,\"visibility\":%d}",
          signature ? signature : "", alpha, visibility);
  if (signature && strstr(signature, "/LocationBarPhone;") && (alpha == 0 || visibility != 0)) *hidden = true;
  release(signature);
  env->DeleteLocalRef(cls);
  if (env->IsInstanceOf(view, view_group)) {
    int children = env->CallIntMethod(view, child_count);
    for (int i = 0; i < children; ++i) {
      jobject child = env->CallObjectMethod(view, child_at, i);
      if (child) { views(env, child, count, hidden); env->DeleteLocalRef(child); }
    }
  }
}
static void JNICALL entered(jvmtiEnv*, JNIEnv* env, jthread thread, jmethodID method) {
  if (method == library_loaded) previous_jni = env->functions;
  if (method == readiness) {
    jobject instance = nullptr;
    toolbar_error = ti->GetLocalInstance(thread, 0, &instance);
    jclass view = env->FindClass("android/view/View");
    if (instance && env->IsInstanceOf(instance, view)) {
      if (current_toolbar) env->DeleteGlobalRef(current_toolbar);
      current_toolbar = env->NewGlobalRef(instance);
    }
    if (instance) env->DeleteLocalRef(instance);
    env->DeleteLocalRef(view);
  }
}
static void JNICALL exited(jvmtiEnv*, JNIEnv* env, jthread, jmethodID method,
                           jboolean exceptional, jvalue value) {
  if (method == library_loaded && previous_jni && env->functions != previous_jni) {
    // Undo Chromium's ARM FindClass callback in the host x86 JNI table for this emulator.
    env->functions = previous_jni;
    pthread_mutex_lock(&lock);
    fputs("{\"event\":\"emulator-jni-restored\"}\n", output); fflush(output);
    pthread_mutex_unlock(&lock);
  }
  if (method != readiness || exceptional || !value.l || !output || syncing) return;
  pthread_mutex_lock(&lock);
  if (!view_group) {
    jclass view = env->FindClass("android/view/View");
    jclass group = env->FindClass("android/view/ViewGroup");
    view_group = static_cast<jclass>(env->NewGlobalRef(group));
    alpha_method = env->GetMethodID(view, "getAlpha", "()F");
    visibility_method = env->GetMethodID(view, "getVisibility", "()I");
    child_count = env->GetMethodID(group, "getChildCount", "()I");
    child_at = env->GetMethodID(group, "getChildAt", "(I)Landroid/view/View;");
    env->DeleteLocalRef(view); env->DeleteLocalRef(group);
  }
  struct timespec stamp;
  clock_gettime(CLOCK_MONOTONIC, &stamp);
  fprintf(output, "{\"event\":\"readiness\",\"time\":%.6f,\"fields\":{", stamp.tv_sec + stamp.tv_nsec / 1e9);
  jclass result_class = env->GetObjectClass(value.l);
  jint count = 0; jfieldID* fields = nullptr; jfieldID ready_field = nullptr;
  bool ready = false, blocked_capture = false;
  int printed = 0;
  if (ti->GetClassFields(result_class, &count, &fields) == JVMTI_ERROR_NONE) {
    for (int i = 0; i < count; ++i) {
      char *name = nullptr, *desc = nullptr; jint modifiers = 0;
      ti->GetFieldName(result_class, fields[i], &name, &desc, nullptr);
      ti->GetFieldModifiers(result_class, fields[i], &modifiers);
      if (name && desc && !(modifiers & 8) && (*desc == 'Z' || *desc == 'I' || *desc == 'B' || *desc == 'S')) {
        if (printed++) fputc(',', output);
        if (*desc == 'Z') {
          ready = env->GetBooleanField(value.l, fields[i]); ready_field = fields[i];
          fprintf(output, "\"%s\":%s", name, ready ? "true" : "false");
        } else {
          int v = *desc == 'B' ? env->GetByteField(value.l, fields[i]) : (*desc == 'S' ? env->GetShortField(value.l, fields[i]) : env->GetIntField(value.l, fields[i])); if (!strcmp(name, "b")) blocked_capture = v == 7 || v == 15;
          fprintf(output, "\"%s\":%d", name, v);
        }
      }
      release(name); release(desc);
    }
    release(fields);
  }
  env->DeleteLocalRef(result_class);
  jobject self = current_toolbar ? env->NewLocalRef(current_toolbar) : nullptr;
  jvmtiError local_error = toolbar_error;
  bool hidden = false; int view_count = 0;
  fprintf(output, "},\"local_error\":%d,\"views\":[", local_error);
  if (self) { views(env, self, &view_count, &hidden); env->DeleteLocalRef(self); }
  bool patch = !ready && blocked_capture && hidden && ready_field && access(patch_path, F_OK) == 0;
  bool refreshed = false;
  if (patch) {
    syncing = true;
    jclass view = env->FindClass("android/view/View");
    jmethodID parent = env->GetMethodID(view, "getParent", "()Landroid/view/ViewParent;");
    jobject ancestor = env->NewLocalRef(current_toolbar);
    for (int depth = 0; ancestor && depth < 8; ++depth) {
      jclass cls = env->GetObjectClass(ancestor);
      char* name = nullptr; ti->GetClassSignature(cls, &name, nullptr);
      bool container = name && !strcmp(name, "Lorg/chromium/chrome/browser/toolbar/top/ToolbarControlContainer;");
      release(name);
      if (container) {
        jmethodID sync = env->GetMethodID(cls, sync_method_name, "(Z)V");
        if (sync) { env->CallVoidMethod(ancestor, sync, JNI_TRUE); refreshed = !env->ExceptionCheck(); }
        if (env->ExceptionCheck()) { env->ExceptionDescribe(); env->ExceptionClear(); }
        env->DeleteLocalRef(cls); break;
      }
      jobject next = env->CallObjectMethod(ancestor, parent);
      env->DeleteLocalRef(cls); env->DeleteLocalRef(ancestor); ancestor = next;
    }
    if (ancestor) env->DeleteLocalRef(ancestor);
    env->DeleteLocalRef(view);
    syncing = false;
  }

  fprintf(output, "],\"experimental_sync\":%s,\"hidden\":%s,\"experimental_patch\":%s}\n", refreshed ? "true" : "false", hidden ? "true" : "false", patch ? "true" : "false");

  fflush(output); pthread_mutex_unlock(&lock);
}
extern "C" JNIEXPORT jint JNICALL Agent_OnAttach(JavaVM* vm, char* options, void*) {
  if (vm->GetEnv(reinterpret_cast<void**>(&ti), JVMTI_VERSION_1_2) != JNI_OK) return JNI_ERR;
  const char* path = options && *options ? options : "/data/data/app.titaniumru.browser.test/files/argon-probe.jsonl";
  output = fopen(path, "w"); if (!output) return JNI_ERR;
  snprintf(patch_path, sizeof(patch_path), "%s-patch", path);
  char method_path[1100]; snprintf(method_path, sizeof(method_path), "%s-sync-method", path);
  FILE* method_file = fopen(method_path, "r");
  if (!method_file || fscanf(method_file, "%127s", sync_method_name) != 1) return JNI_ERR;
  fclose(method_file);
  jvmtiCapabilities potential = {}, caps = {};
  ti->GetPotentialCapabilities(&potential);
  caps.can_generate_method_exit_events = potential.can_generate_method_exit_events;
  caps.can_generate_method_entry_events = potential.can_generate_method_entry_events;
  caps.can_access_local_variables = potential.can_access_local_variables;
  jvmtiError capabilities = ti->AddCapabilities(&caps);
  jvmtiError table = ti->GetJNIFunctionTable(&original_jni);
  fprintf(output, "{\"event\":\"attach\",\"capabilities\":%d,\"method_exit\":%d,\"locals\":%d,\"jni_table\":%d}\n",
          capabilities, caps.can_generate_method_exit_events, caps.can_access_local_variables, table);
  fflush(output);
  jvmtiEventCallbacks callbacks = {};
  callbacks.ClassPrepare = prepared; callbacks.MethodEntry = entered; callbacks.MethodExit = exited;
  ti->SetEventCallbacks(&callbacks, offsetof(jvmtiEventCallbacks, MethodExit) + sizeof(callbacks.MethodExit));
  ti->SetEventNotificationMode(JVMTI_ENABLE, JVMTI_EVENT_CLASS_PREPARE, nullptr);
  jint count = 0; jclass* classes = nullptr;
  if (ti->GetLoadedClasses(&count, &classes) == JVMTI_ERROR_NONE) {
    for (int i = 0; i < count; ++i) remember(classes[i]);
    release(classes);
  }
  jthread* threads = nullptr;
  if (ti->GetAllThreads(&count, &threads) == JVMTI_ERROR_NONE) {
    JNIEnv* env = nullptr; vm->GetEnv(reinterpret_cast<void**>(&env), JNI_VERSION_1_6);
    for (int i = 0; i < count; ++i) {
      jvmtiThreadInfo info = {};
      if (ti->GetThreadInfo(threads[i], &info) == JVMTI_ERROR_NONE) {
        if (info.name && !strcmp(info.name, "main")) {
          ti->SetEventNotificationMode(JVMTI_ENABLE, JVMTI_EVENT_METHOD_ENTRY, threads[i]);
          ti->SetEventNotificationMode(JVMTI_ENABLE, JVMTI_EVENT_METHOD_EXIT, threads[i]);
        }
        release(info.name);
        if (info.thread_group) env->DeleteLocalRef(info.thread_group);
        if (info.context_class_loader) env->DeleteLocalRef(info.context_class_loader);
      }
      env->DeleteLocalRef(threads[i]);
    }
    release(threads);
  }
  return JNI_OK;
}
