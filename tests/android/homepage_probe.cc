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
static pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;
static jmethodID readiness, library_loaded;
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
static void JNICALL entered(jvmtiEnv*, JNIEnv* env, jthread, jmethodID method) {
  if (method == library_loaded) previous_jni = env->functions;
}
static void JNICALL exited(jvmtiEnv*, JNIEnv* env, jthread thread, jmethodID method,
                           jboolean exceptional, jvalue value) {
  if (method == library_loaded && previous_jni && env->functions != previous_jni) {
    // Undo Chromium's ARM FindClass callback in the host x86 JNI table for this emulator.
    env->functions = previous_jni;
    pthread_mutex_lock(&lock);
    fputs("{\"event\":\"emulator-jni-restored\"}\n", output); fflush(output);
    pthread_mutex_unlock(&lock);
  }
  if (method != readiness || exceptional || !value.l || !output) return;
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
  bool ready = false, status_animation = false;
  int printed = 0;
  if (ti->GetClassFields(result_class, &count, &fields) == JVMTI_ERROR_NONE) {
    for (int i = 0; i < count; ++i) {
      char *name = nullptr, *desc = nullptr; jint modifiers = 0;
      ti->GetFieldName(result_class, fields[i], &name, &desc, nullptr);
      ti->GetFieldModifiers(result_class, fields[i], &modifiers);
      if (name && desc && !(modifiers & 8) && (*desc == 'Z' || *desc == 'I')) {
        if (printed++) fputc(',', output);
        if (*desc == 'Z') {
          ready = env->GetBooleanField(value.l, fields[i]); ready_field = fields[i];
          fprintf(output, "\"%s\":%s", name, ready ? "true" : "false");
        } else {
          int v = env->GetIntField(value.l, fields[i]); status_animation |= v == 7;
          fprintf(output, "\"%s\":%d", name, v);
        }
      }
      release(name); release(desc);
    }
    release(fields);
  }
  env->DeleteLocalRef(result_class);
  jobject self = nullptr;
  jvmtiError local_error = ti->GetLocalInstance(thread, 0, &self);
  bool hidden = false; int view_count = 0;
  fprintf(output, "},\"local_error\":%d,\"views\":[", local_error);
  if (self) { views(env, self, &view_count, &hidden); env->DeleteLocalRef(self); }
  bool patch = !ready && status_animation && hidden && ready_field && access(patch_path, F_OK) == 0;
  if (patch) env->SetBooleanField(value.l, ready_field, JNI_TRUE);
  fprintf(output, "],\"hidden\":%s,\"experimental_patch\":%s}\n", hidden ? "true" : "false", patch ? "true" : "false");
  fflush(output); pthread_mutex_unlock(&lock);
}
extern "C" JNIEXPORT jint JNICALL Agent_OnAttach(JavaVM* vm, char* options, void*) {
  if (vm->GetEnv(reinterpret_cast<void**>(&ti), JVMTI_VERSION_1_2) != JNI_OK) return JNI_ERR;
  const char* path = options && *options ? options : "/data/data/app.titaniumru.browser.test/files/argon-probe.jsonl";
  output = fopen(path, "w"); if (!output) return JNI_ERR;
  snprintf(patch_path, sizeof(patch_path), "%s-patch", path);
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
