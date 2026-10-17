#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include "llama.h"

static void json_string(const char *value) {
    putchar('"');
    for (const unsigned char *p=(const unsigned char *)value; *p; ++p) {
        if (*p=='"' || *p=='\\') { putchar('\\'); putchar(*p); }
        else if (*p=='\n') fputs("\\n", stdout);
        else if (*p=='\r') fputs("\\r", stdout);
        else if (*p=='\t') fputs("\\t", stdout);
        else putchar(*p);
    }
    putchar('"');
}

static int generate(const char *model_path, const char *prompt, int max_tokens) {
    llama_backend_init();
    struct llama_model_params mp=llama_model_default_params();
    mp.n_gpu_layers=0;
    struct llama_model *model=llama_model_load_from_file(model_path,mp);
    if (!model) { fputs("{\"type\":\"error\",\"message\":\"model load failed\"}\n",stdout); llama_backend_free(); return 2; }
    struct llama_context_params cp=llama_context_default_params(); cp.n_ctx=2048; cp.n_batch=512;
    struct llama_context *ctx=llama_init_from_model(model,cp);
    if (!ctx) { fputs("{\"type\":\"error\",\"message\":\"context initialization failed\"}\n",stdout); llama_model_free(model); llama_backend_free(); return 3; }
    const struct llama_vocab *vocab=llama_model_get_vocab(model); int32_t count=llama_tokenize(vocab,prompt,(int32_t)strlen(prompt),NULL,0,true,false); if (count<0) count=-count;
    if (count<=0 || count>2000) { fputs("{\"type\":\"error\",\"message\":\"prompt tokenization failed or exceeds context\"}\n",stdout); llama_free(ctx); llama_model_free(model); llama_backend_free(); return 4; }
    llama_token *tokens=calloc((size_t)count,sizeof(*tokens)); llama_tokenize(vocab,prompt,(int32_t)strlen(prompt),tokens,count,true,false);
    struct llama_batch batch=llama_batch_init(512,0,1);
    for (int32_t i=0;i<count;i++) { batch.token[i]=tokens[i]; batch.pos[i]=i; batch.n_seq_id[i]=1; batch.seq_id[i][0]=0; batch.logits[i]=(i==count-1); }
    batch.n_tokens=count;
    if (llama_decode(ctx,batch)!=0) { fputs("{\"type\":\"error\",\"message\":\"prompt evaluation failed\"}\n",stdout); llama_batch_free(batch); free(tokens); llama_free(ctx); llama_model_free(model); llama_backend_free(); return 5; }
    struct llama_sampler *sampler=llama_sampler_chain_init(llama_sampler_chain_default_params()); llama_sampler_chain_add(sampler,llama_sampler_init_greedy());
    printf("{\"type\":\"meta\",\"model\":"); json_string(model_path); printf(",\"context\":%u}\n",llama_n_ctx(ctx));
    for (int i=0;i<max_tokens;i++) { llama_token token=llama_sampler_sample(sampler,ctx,-1); if (llama_vocab_is_eog(vocab,token)) break; char piece[512]; int n=llama_token_to_piece(vocab,token,piece,(int)sizeof(piece)-1,0,false); if(n<0)n=0; piece[n]='\0'; printf("{\"type\":\"token\",\"text\":"); json_string(piece); printf("}\n"); fflush(stdout); struct llama_batch next=llama_batch_get_one(&token,1); if(llama_decode(ctx,next)!=0)break; }
    fputs("{\"type\":\"complete\",\"finish_reason\":\"stop\"}\n",stdout); llama_sampler_free(sampler); llama_batch_free(batch); free(tokens); llama_free(ctx); llama_model_free(model); llama_backend_free(); return 0;
}

int main(int argc,char **argv) { if(argc<3){fprintf(stderr,"usage: xerxes-llm MODEL PROMPT [MAX_TOKENS]\n");return 64;} int n=argc>3?atoi(argv[3]):128; if(n<1||n>2048)n=128; return generate(argv[1],argv[2],n); }
