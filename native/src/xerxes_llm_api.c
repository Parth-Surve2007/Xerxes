#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "llama.h"

#if defined(_WIN32)
#define XERXES_API __declspec(dllexport) __cdecl
#else
#define XERXES_API
#endif

typedef struct xerxes_model { struct llama_model *model; struct llama_context *ctx; int has_generation; } xerxes_model;
static void set_error(char *out, size_t cap, const char *msg) { if (out && cap) { strncpy(out,msg,cap-1); out[cap-1]='\0'; } }
static int copy_output(char *out,size_t cap,const char *text) { size_t n=strlen(text); if(!out||cap<=n)return 0; memcpy(out,text,n+1); return (int)n; }

XERXES_API int xerxes_model_create(const char *path, uint32_t threads, void **handle, char *error, size_t error_cap) {
    if (!path || !handle) { set_error(error,error_cap,"invalid arguments"); return 1; }
    *handle=NULL; llama_backend_init();
    struct llama_model_params mp=llama_model_default_params(); mp.n_gpu_layers=0;
    struct llama_model *model=llama_model_load_from_file(path,mp); if(!model){set_error(error,error_cap,"model load failed");llama_backend_free();return 2;}
    struct llama_context_params cp=llama_context_default_params(); cp.n_ctx=2048; cp.n_batch=512; if(threads)cp.n_threads=(int32_t)threads; cp.n_threads_batch=cp.n_threads;
    struct llama_context *ctx=llama_init_from_model(model,cp); if(!ctx){set_error(error,error_cap,"context initialization failed");llama_model_free(model);llama_backend_free();return 3;}
    xerxes_model *m=(xerxes_model*)calloc(1,sizeof(*m)); if(!m){set_error(error,error_cap,"allocation failed");llama_free(ctx);llama_model_free(model);llama_backend_free();return 4;} m->model=model;m->ctx=ctx;*handle=m;return 0;
}

XERXES_API int xerxes_model_generate(void *handle,const char *prompt,uint32_t max_tokens,char *out,size_t out_cap,char *error,size_t error_cap) {
    if(!handle||!prompt||!out||!out_cap){set_error(error,error_cap,"invalid arguments");return 1;} if(max_tokens<1||max_tokens>256)max_tokens=64;
    xerxes_model *m=(xerxes_model*)handle; const struct llama_vocab *v=llama_model_get_vocab(m->model); int32_t n=llama_tokenize(v,prompt,(int32_t)strlen(prompt),NULL,0,true,false); if(n<0)n=-n;
    if(n<=0||n>2000){set_error(error,error_cap,"prompt exceeds context or tokenization failed");return 2;}
    llama_token *tokens=(llama_token*)calloc((size_t)n,sizeof(*tokens)); if(!tokens){set_error(error,error_cap,"allocation failed");return 3;} llama_tokenize(v,prompt,(int32_t)strlen(prompt),tokens,n,true,false);
    if(m->has_generation) llama_memory_clear(llama_get_memory(m->ctx),true); struct llama_batch batch=llama_batch_init(512,0,1); for(int32_t i=0;i<n;i++){batch.token[i]=tokens[i];batch.pos[i]=i;batch.n_seq_id[i]=1;batch.seq_id[i][0]=0;batch.logits[i]=(i==n-1);} batch.n_tokens=n;
    if(llama_decode(m->ctx,batch)!=0){set_error(error,error_cap,"prompt evaluation failed");llama_batch_free(batch);free(tokens);return 4;}
    struct llama_sampler *s=llama_sampler_chain_init(llama_sampler_chain_default_params());llama_sampler_chain_add(s,llama_sampler_init_greedy()); size_t used=0;
    for(uint32_t i=0;i<max_tokens;i++){llama_token t=llama_sampler_sample(s,m->ctx,-1);if(llama_vocab_is_eog(v,t))break;char piece[512];int k=llama_token_to_piece(v,t,piece,(int)sizeof(piece)-1,0,false);if(k<0)k=0;piece[k]='\0';if(used+(size_t)k+1>=out_cap){set_error(error,error_cap,"output buffer too small");llama_sampler_free(s);llama_batch_free(batch);free(tokens);return 5;}memcpy(out+used,piece,(size_t)k);used+=(size_t)k;out[used]='\0';struct llama_batch next=llama_batch_get_one(&t,1);if(llama_decode(m->ctx,next)!=0)break;}
    llama_sampler_free(s);llama_batch_free(batch);free(tokens);m->has_generation=1;return (int)used;
}

XERXES_API const char *xerxes_model_error(void) { return "see per-call error buffer"; }
XERXES_API void xerxes_model_destroy(void *handle) { if(!handle)return; xerxes_model *m=(xerxes_model*)handle;llama_free(m->ctx);llama_model_free(m->model);free(m);llama_backend_free(); }
